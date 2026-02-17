"""Main Alethic agent orchestrator (mathematics).

Implements the Generate → Verify → Revise loop inspired by Google DeepMind's
Aletheia architecture. PhysicsAgent in physics_agent.py inherits this loop
and injects physics-specific prompt templates. The loop continues until:
  1. The Verifier approves the solution (verdict = CORRECT, confidence ≥ threshold), or
  2. The maximum iteration limit is reached (strategic failure admission).

Architecture diagram:

    ┌─────────────────────────────────────────────────────────┐
    │                    Orchestrator Loop                     │
    │                                                         │
    │   ┌───────────┐    ┌──────────┐    ┌──────────┐       │
    │   │ Generator  │───▶│ Verifier │───▶│ Reviser  │──┐    │
    │   └───────────┘    └──────────┘    └──────────┘  │    │
    │        ▲                                          │    │
    │        └──────────────────────────────────────────┘    │
    │                                                         │
    │   Terminates when: CORRECT verdict OR max iterations    │
    └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import anthropic

from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    EventType,
    Solution,
    Verdict,
    VerificationResult,
)
from alethic.subagents import generate, revise, verify

logger = logging.getLogger("alethic")

_EMPTY_REASONS = frozenset({"n/a", "na", "none", "not applicable", ""})


@dataclass
class RunState:
    """Mutable state accumulated across iterations of the GVR loop."""

    total_revisions: int = 0
    best_solution: Solution | None = None
    best_confidence: float = 0.0
    failed_approaches: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)


@dataclass
class EventLog:
    """Append-only event log for the GVR loop."""

    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type: EventType, iteration: int, **data) -> None:
        self.events.append(AgentEvent(type=type, iteration=iteration, data=data))


class MathAgent:
    """Alethic-style mathematical reasoning agent powered by Claude.

    Implements the three-subagent Generate → Verify → Revise architecture
    with decoupled verification for robust mathematical problem solving.

    Usage:
        agent = MathAgent()  # uses ANTHROPIC_API_KEY
        result = agent.solve("Prove that there are infinitely many primes.")
        print(result)
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        api_key: str | None = None,
    ):
        self.config = config or AgentConfig()
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self._setup_logging()

    def _setup_logging(self) -> None:
        if self.config.verbose and not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

    def _prompt_set(self) -> dict[str, str]:
        """Return domain-specific prompt overrides for subagents.

        Override in subclasses to inject different prompts.
        Keys: generator_system, generator_user, balanced_addendum,
              verifier_system, verifier_user, reviser_system, reviser_user.
        """
        return {}

    def _log_header(self) -> str:
        return "ALETHIC MATH AGENT"

    def _check_false_premise(
        self,
        verification: VerificationResult,
        problem: str,
        iteration: int,
        state: RunState,
        log: EventLog,
    ) -> AgentResult | None:
        """Return AgentResult if verifier detected a false premise, else None."""
        if (
            verification.verdict == Verdict.UNSOLVED
            and verification.reason
            and verification.reason.strip().lower() not in _EMPTY_REASONS
        ):
            self._log("")
            self._log("[FALSE PREMISE] Verifier detected the problem's premise is false:")
            self._log(f"  {verification.reason}")
            elapsed = time.time() - state.start_time
            return AgentResult(
                problem=problem,
                solution=verification.reason,
                verdict=Verdict.UNSOLVED,
                confidence=verification.confidence,
                iterations_used=iteration,
                total_revisions=state.total_revisions,
                admitted_failure=False,
                events=log.events,
                elapsed_seconds=elapsed,
                candidates_per_iteration=self.config.best_of_n,
                failed_approaches=state.failed_approaches,
            )
        return None

    def _generate_candidates(
        self,
        *,
        problem: str,
        iteration: int,
        balanced: bool,
        prompts: dict[str, str],
        n: int,
    ) -> list[tuple[Solution, float]]:
        """Generate N candidates. Parallel (ThreadPoolExecutor) when N>1, sequential when N=1."""

        def _gen_one() -> tuple[Solution, float]:
            t0 = time.time()
            sol = generate(
                self.client,
                problem=problem,
                config=self.config,
                iteration=iteration,
                balanced=balanced,
                system_prompt=prompts.get("generator_system"),
                user_template=prompts.get("generator_user"),
                balanced_addendum=prompts.get("balanced_addendum"),
            )
            return sol, time.time() - t0

        if n == 1:
            return [_gen_one()]

        results: list[tuple[Solution, float]] = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {pool.submit(_gen_one): i for i in range(1, n + 1)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning("Candidate %d failed: %s", idx, e)
        return results

    def _verify_candidates(
        self,
        *,
        problem: str,
        candidates: list[tuple[Solution, float]],
        prompts: dict[str, str],
    ) -> list[tuple[Solution, VerificationResult, float, float]]:
        """Verify all candidates sequentially. Return list sorted by confidence desc.

        Returns list of (solution, verification, gen_time, verify_time) tuples.
        """
        verified: list[tuple[Solution, VerificationResult, float, float]] = []
        for solution, gen_time in candidates:
            t0 = time.time()
            verification = verify(
                self.client,
                problem=problem,
                solution=solution,
                config=self.config,
                system_prompt=prompts.get("verifier_system"),
                user_template=prompts.get("verifier_user"),
            )
            verify_time = time.time() - t0
            verified.append((solution, verification, gen_time, verify_time))

        # Sort by confidence descending
        verified.sort(key=lambda x: x[1].confidence, reverse=True)
        return verified

    def _log_candidates(
        self,
        verified: list[tuple[Solution, VerificationResult, float, float]],
        generation_wall_time: float,
    ) -> None:
        """Log generation wall time and a ranked table of candidates (N>1 only)."""
        self._log(f"[BEST-OF-N] Generated {len(verified)} candidates "
                   f"(wall time: {generation_wall_time:.1f}s)")
        self._log(f"{'Rank':<6}{'Verdict':<16}{'Confidence':<13}{'Gen(s)':<10}{'Ver(s)':<10}")
        self._log(f"{'─' * 55}")
        for rank, (_sol, ver, gen_t, ver_t) in enumerate(verified, 1):
            self._log(f"{rank:<6}{ver.verdict.value:<16}{ver.confidence:<13.0%}"
                       f"{gen_t:<10.1f}{ver_t:<10.1f}")

    def _run_revision_loop(
        self,
        *,
        problem: str,
        solution: Solution,
        verification: VerificationResult,
        prompts: dict[str, str],
        iteration: int,
        state: RunState,
        log: EventLog,
        threshold: float,
    ) -> AgentResult | None:
        """Run revision sub-loop. Returns AgentResult if solved, else None (mutates state)."""
        current_solution = solution

        for rev_num in range(1, self.config.max_revisions_per_cycle + 1):
            self._log(f"[REVISE] Revision {rev_num}/{self.config.max_revisions_per_cycle}...")
            current_solution = revise(
                self.client,
                problem=problem,
                solution=current_solution,
                verification=verification,
                config=self.config,
                revision_number=rev_num,
                system_prompt=prompts.get("reviser_system"),
                user_template=prompts.get("reviser_user"),
            )
            state.total_revisions += 1
            log.emit(
                EventType.REVISE,
                iteration,
                revision=rev_num,
            )

            # Re-verify the revision
            self._log(f"[VERIFY] Re-verifying revision {rev_num}...")
            verification = verify(
                self.client,
                problem=problem,
                solution=current_solution,
                config=self.config,
                system_prompt=prompts.get("verifier_system"),
                user_template=prompts.get("verifier_user"),
            )
            log.emit(
                EventType.VERIFY,
                iteration,
                revision=rev_num,
                verdict=verification.verdict.value,
                confidence=verification.confidence,
            )
            self._log(f"[VERIFY] Verdict: {verification.verdict.value} "
                       f"(confidence: {verification.confidence:.0%})")

            if verification.confidence > state.best_confidence:
                state.best_confidence = verification.confidence
                state.best_solution = current_solution

            if verification.is_acceptable(threshold):
                self._log("")
                self._log("[SOLVED] Verifier approved the revised solution!")
                elapsed = time.time() - state.start_time
                return AgentResult(
                    problem=problem,
                    solution=current_solution.solution_text,
                    verdict=Verdict.CORRECT,
                    confidence=verification.confidence,
                    iterations_used=iteration,
                    total_revisions=state.total_revisions,
                    admitted_failure=False,
                    events=log.events,
                    elapsed_seconds=elapsed,
                    candidates_per_iteration=self.config.best_of_n,
                    failed_approaches=state.failed_approaches,
                )

            # If major flaw after revision, break to restart from generator
            if verification.verdict == Verdict.MAJOR_FLAW:
                self._log("[REVISE] Major flaw persists — restarting from generator")
                break

            # If unsolved after revision, check false premise then restart
            if verification.verdict == Verdict.UNSOLVED:
                fp = self._check_false_premise(
                    verification, problem, iteration,
                    state, log,
                )
                if fp:
                    return fp
                self._log("[REVISE] Solution unsolvable — restarting from generator")
                break
        else:
            self._log(f"[REVISE] Exhausted revision attempts for iteration {iteration}")

        return None

    def solve(
        self,
        problem: str,
        balanced: bool = True,
    ) -> AgentResult:
        """Solve a mathematical problem using the Generate → Verify → Revise loop.

        Args:
            problem: The mathematical problem statement.
            balanced: Use balanced prompting (explore counterexamples first).

        Returns:
            AgentResult with the solution (or admitted failure).
        """
        state = RunState()
        log = EventLog()
        threshold = self.config.confidence_threshold
        prompts = self._prompt_set()

        n = self.config.best_of_n

        self._log(f"{'=' * 60}")
        self._log(self._log_header())
        self._log(f"Model: {self.config.model}")
        self._log(f"Max iterations: {self.config.max_iterations}")
        self._log(f"Confidence threshold: {threshold:.0%}")
        self._log(f"Code execution: {'enabled' if self.config.enable_code_execution else 'disabled'}")
        if n > 1:
            self._log(f"Best-of-N: {n} candidates per iteration (parallel)")
        self._log(f"{'=' * 60}")
        self._log(f"Problem: {problem[:200]}{'...' if len(problem) > 200 else ''}")
        self._log("")

        for iteration in range(1, self.config.max_iterations + 1):
            self._log(f"{'─' * 40}")
            self._log(f"Iteration {iteration}/{self.config.max_iterations}")
            self._log(f"{'─' * 40}")

            try:
                # ── GENERATE ──
                if n > 1:
                    self._log(f"[GENERATE] Producing {n} candidates (parallel)...")
                else:
                    self._log("[GENERATE] Producing candidate solution...")

                gen_t0 = time.time()
                candidates = self._generate_candidates(
                    problem=problem,
                    iteration=iteration,
                    balanced=balanced,
                    prompts=prompts,
                    n=n,
                )
                gen_wall_time = time.time() - gen_t0

                if not candidates:
                    self._log("[GENERATE] All candidates failed — skipping iteration")
                    log.emit(EventType.ERROR, iteration, error="all candidates failed")
                    continue

                if len(candidates) < n:
                    failures = n - len(candidates)
                    logger.info(
                        "Generated %d/%d candidates (%d failed)",
                        len(candidates), n, failures,
                    )
                    self._log(f"[GENERATE] Warning: {len(candidates)}/{n} candidates "
                               f"succeeded ({failures} failed)")

                for idx, (sol, gen_t) in enumerate(candidates, 1):
                    log.emit(
                        EventType.GENERATE,
                        iteration,
                        candidate=idx,
                        solution_preview=sol.solution_text[:500],
                    )
                    self._log(f"[GENERATE] Candidate {idx}/{len(candidates)} produced "
                               f"({len(sol.solution_text)} chars, {gen_t:.1f}s)")

                # ── VERIFY (decoupled) ──
                self._log("[VERIFY] Independently verifying...")
                verified = self._verify_candidates(
                    problem=problem,
                    candidates=candidates,
                    prompts=prompts,
                )

                # Log rankings for N>1
                if n > 1:
                    self._log_candidates(verified, gen_wall_time)

                # Record all in log
                for idx, (_sol, ver, _gen_t, _ver_t) in enumerate(verified, 1):
                    log.emit(
                        EventType.VERIFY,
                        iteration,
                        candidate=idx,
                        verdict=ver.verdict.value,
                        confidence=ver.confidence,
                        num_issues=len(ver.issues),
                    )

                # Best candidate is first (sorted by confidence desc)
                solution, verification, _, _ = verified[0]

                self._log(f"[VERIFY] Best: {verification.verdict.value} "
                           f"(confidence: {verification.confidence:.0%})")
                if verification.issues:
                    for issue in verification.issues:
                        self._log(f"  Issue: {str(issue)[:100]}")

                # Track best solution seen
                if verification.confidence > state.best_confidence:
                    state.best_confidence = verification.confidence
                    state.best_solution = solution

                # ── CHECK: Is it correct? ──
                if verification.is_acceptable(threshold):
                    self._log("")
                    self._log("[SOLVED] Verifier approved the solution!")
                    elapsed = time.time() - state.start_time
                    return AgentResult(
                        problem=problem,
                        solution=solution.solution_text,
                        verdict=Verdict.CORRECT,
                        confidence=verification.confidence,
                        iterations_used=iteration,
                        total_revisions=state.total_revisions,
                        admitted_failure=False,
                        events=log.events,
                        elapsed_seconds=elapsed,
                        candidates_per_iteration=n,
                        failed_approaches=state.failed_approaches,
                    )

                # ── CHECK: False premise? ──
                fp = self._check_false_premise(
                    verification, problem, iteration,
                    state, log,
                )
                if fp:
                    return fp

                # ── REVISE (best candidate) ──
                if verification.needs_revision(threshold):
                    result = self._run_revision_loop(
                        problem=problem,
                        solution=solution,
                        verification=verification,
                        prompts=prompts,
                        iteration=iteration,
                        state=state,
                        log=log,
                        threshold=threshold,
                    )
                    if isinstance(result, AgentResult):
                        return result
                else:
                    self._log("[GENERATE] Solution unsolvable in this attempt — will retry from scratch")

            except anthropic.APIError as e:
                logger.warning("Iteration %d failed: %s", iteration, e)
                log.emit(EventType.ERROR, iteration, error=str(e))
                continue

        # ── FAILURE ADMISSION ──
        elapsed = time.time() - state.start_time
        self._log("")
        self._log(f"{'=' * 60}")
        self._log("[ADMITTED FAILURE] Exhausted all iterations without verified solution")
        self._log(f"Best confidence seen: {state.best_confidence:.0%}")
        self._log(f"{'=' * 60}")

        return AgentResult(
            problem=problem,
            solution=state.best_solution.solution_text if state.best_solution else None,
            verdict=Verdict.UNSOLVED,
            confidence=state.best_confidence,
            iterations_used=self.config.max_iterations,
            total_revisions=state.total_revisions,
            admitted_failure=True,
            events=log.events,
            elapsed_seconds=elapsed,
            candidates_per_iteration=n,
            failed_approaches=state.failed_approaches,
        )

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message)
