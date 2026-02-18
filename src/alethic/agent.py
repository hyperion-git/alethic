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
from collections import deque
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
from alethic.prompts import (
    BALANCED_GENERATOR_ADDENDUM,
    GENERATOR_SYSTEM,
    TOOL_GUIDANCE,
    VERIFIER_SYSTEM,
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
    # Stall detection state
    iterations_since_meaningful_improvement: int = 0
    iteration_final_verdicts: deque = field(default_factory=lambda: deque(maxlen=3))
    resets_used: int = 0
    reset_cooldown_remaining: int = 0


@dataclass
class EventLog:
    """Append-only event log for the GVR loop."""

    events: list[AgentEvent] = field(default_factory=list)

    def emit(self, type: EventType, iteration: int, **data) -> None:
        self.events.append(AgentEvent(type=type, iteration=iteration, data=data))


def _summarize_failed_approach(verification: VerificationResult) -> str:
    """Extract a one-line summary of a failed approach from a verification result."""
    # First sentence of critique
    critique = verification.critique.strip()
    first_sentence_end = critique.find(". ")
    summary = critique[: first_sentence_end + 1] if first_sentence_end > 0 else critique[:150]

    # Append top issue if available
    if verification.issues:
        top_issue = str(verification.issues[0])
        summary = f"{summary} Issue: {top_issue}"

    return summary[:200]


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

    def _get_tool_guidance_map(self) -> dict:
        """Return the tool guidance map for this domain.

        Override in subclasses to use domain-specific tool guidance.
        """
        return TOOL_GUIDANCE

    def _build_system_prompt(self, role: str, base: str) -> str:
        """Append tool guidance overlays to a base system prompt."""
        system = base
        guide_map = self._get_tool_guidance_map()
        for tool in sorted(self.config.tool_guidance):
            if tool in guide_map and role in guide_map[tool]:
                system += guide_map[tool][role]
        return system

    def _reset_addendum(self) -> str:
        """Return the strategy reset prompt template for this domain.

        Override in subclasses to use domain-specific reset prompts.
        """
        from alethic.prompts import STRATEGY_RESET_ADDENDUM

        return STRATEGY_RESET_ADDENDUM

    def _check_stall(self, state: RunState) -> bool:
        """Check whether a stall-triggered reset should fire this iteration."""
        if not self.config.stall_reset:
            return False
        if state.reset_cooldown_remaining > 0:
            return False
        max_resets = max(1, self.config.max_iterations // 4)
        if state.resets_used >= max_resets:
            return False

        # Detector 1: no meaningful progress for stall_window iterations
        if state.iterations_since_meaningful_improvement >= self.config.stall_window:
            return True

        # Detector 2: last 2 iteration-final verdicts are both MAJOR_FLAW
        verdicts = state.iteration_final_verdicts
        return (
            len(verdicts) >= 2
            and verdicts[-1] == Verdict.MAJOR_FLAW
            and verdicts[-2] == Verdict.MAJOR_FLAW
        )

    def _build_reset_context(self, failed_approaches: list[str]) -> str:
        """Build the strategy-reset prompt overlay for a reset iteration."""
        recent = failed_approaches[-2:] if len(failed_approaches) > 2 else failed_approaches
        approaches_text = "\n".join(f"- {a}" for a in recent) if recent else "- (none recorded)"
        return self._reset_addendum().format(failed_approaches=approaches_text)

    def _log_header(self) -> str:
        return "ALETHIC MATH AGENT"

    def _make_result(
        self,
        *,
        problem: str,
        solution: str | None,
        verdict: Verdict,
        confidence: float,
        iterations_used: int,
        admitted_failure: bool,
        state: RunState,
        log: EventLog,
    ) -> AgentResult:
        """Build an AgentResult with fields common to all exit paths."""
        return AgentResult(
            problem=problem,
            solution=solution,
            verdict=verdict,
            confidence=confidence,
            iterations_used=iterations_used,
            total_revisions=state.total_revisions,
            admitted_failure=admitted_failure,
            events=log.events,
            elapsed_seconds=time.time() - state.start_time,
            candidates_per_iteration=self.config.best_of_n,
            failed_approaches=state.failed_approaches,
        )

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
            return self._make_result(
                problem=problem,
                solution=verification.reason,
                verdict=Verdict.UNSOLVED,
                confidence=verification.confidence,
                iterations_used=iteration,
                admitted_failure=False,
                state=state,
                log=log,
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
        failed_approaches: tuple[str, ...] = (),
        reset_context: str | None = None,
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
                failed_approaches=failed_approaches,
                reset_context=reset_context,
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
    ) -> list[tuple[Solution, VerificationResult, float, float, int]]:
        """Verify all candidates sequentially. Return list sorted by confidence desc.

        Returns list of (solution, verification, gen_time, verify_time, orig_idx)
        tuples where orig_idx is the 1-based generation-order index.
        """
        verified: list[tuple[Solution, VerificationResult, float, float, int]] = []
        for idx, (solution, gen_time) in enumerate(candidates, 1):
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
            verified.append((solution, verification, gen_time, verify_time, idx))

        # Sort by confidence descending
        verified.sort(key=lambda x: x[1].confidence, reverse=True)
        return verified

    def _log_candidates(
        self,
        verified: list[tuple[Solution, VerificationResult, float, float, int]],
        generation_wall_time: float,
    ) -> None:
        """Log generation wall time and a ranked table of candidates (N>1 only)."""
        self._log(f"[BEST-OF-N] Generated {len(verified)} candidates "
                   f"(wall time: {generation_wall_time:.1f}s)")
        self._log(f"{'Rank':<6}{'Verdict':<16}{'Confidence':<13}{'Gen(s)':<10}{'Ver(s)':<10}")
        self._log(f"{'─' * 55}")
        for rank, (_sol, ver, gen_t, ver_t, _orig_idx) in enumerate(verified, 1):
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
        max_revisions: int | None = None,
    ) -> AgentResult | None:
        """Run revision sub-loop. Returns AgentResult if solved, else None (mutates state)."""
        effective_max_revisions = max_revisions if max_revisions is not None else self.config.max_revisions_per_cycle
        current_solution = solution

        for rev_num in range(1, effective_max_revisions + 1):
            self._log(f"[REVISE] Revision {rev_num}/{effective_max_revisions}...")
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
                log.emit(
                    EventType.ACCEPT,
                    iteration,
                    confidence=verification.confidence,
                    verdict=verification.verdict.value,
                )
                return self._make_result(
                    problem=problem,
                    solution=current_solution.solution_text,
                    verdict=Verdict.CORRECT,
                    confidence=verification.confidence,
                    iterations_used=iteration,
                    admitted_failure=False,
                    state=state,
                    log=log,
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

        # Build generator system prompt with tool guidance
        gen_base = prompts.get("generator_system") or GENERATOR_SYSTEM
        prompts["generator_system"] = self._build_system_prompt("generator", gen_base)

        # Build verifier system prompt with tool guidance
        ver_base = prompts.get("verifier_system") or VERIFIER_SYSTEM
        prompts["verifier_system"] = self._build_system_prompt("verifier", ver_base)

        self._log(f"{'=' * 60}")
        self._log(self._log_header())
        self._log(f"Model: {self.config.model}")
        self._log(f"Max iterations: {self.config.max_iterations}")
        self._log(f"Confidence threshold: {threshold:.0%}")
        self._log(f"Code execution: {'enabled' if self.config.enable_code_execution else 'disabled'}")
        if self.config.best_of_n > 1:
            self._log(f"Best-of-N: {self.config.best_of_n} candidates per iteration (parallel)")
        if self.config.stall_reset:
            self._log(f"Stall reset: enabled (window={self.config.stall_window}, "
                       f"epsilon={self.config.stall_epsilon})")
        self._log(f"{'=' * 60}")
        self._log(f"Problem: {problem[:200]}{'...' if len(problem) > 200 else ''}")
        self._log("")

        for iteration in range(1, self.config.max_iterations + 1):
            self._log(f"{'─' * 40}")
            self._log(f"Iteration {iteration}/{self.config.max_iterations}")
            self._log(f"{'─' * 40}")

            try:
                pre_iter_best = state.best_confidence

                # ── STALL CHECK ──
                is_reset = self._check_stall(state)
                if is_reset:
                    reason = "major_flaw_streak" if (
                        len(state.iteration_final_verdicts) >= 2
                        and state.iteration_final_verdicts[-1] == Verdict.MAJOR_FLAW
                        and state.iteration_final_verdicts[-2] == Verdict.MAJOR_FLAW
                    ) else "no_progress"
                    state.resets_used += 1
                    state.reset_cooldown_remaining = 1
                    n_this_iter = self.config.best_of_n + self.config.reset_n_boost
                    reset_context = self._build_reset_context(state.failed_approaches)
                    self._log(f"[STALL RESET] Triggered (reason: {reason}) — "
                              f"N={n_this_iter}, max_revisions=1")
                    log.emit(
                        EventType.STALL_RESET,
                        iteration,
                        reason=reason,
                        n_override=n_this_iter,
                        max_revisions_override=1,
                        resets_used=state.resets_used,
                        stall_counter=state.iterations_since_meaningful_improvement,
                    )
                else:
                    n_this_iter = self.config.best_of_n
                    reset_context = None
                    if state.reset_cooldown_remaining > 0:
                        state.reset_cooldown_remaining -= 1

                # ── GENERATE ──
                if n_this_iter > 1:
                    self._log(f"[GENERATE] Producing {n_this_iter} candidates (parallel)...")
                else:
                    self._log("[GENERATE] Producing candidate solution...")

                gen_t0 = time.time()
                candidates = self._generate_candidates(
                    problem=problem,
                    iteration=iteration,
                    balanced=balanced,
                    prompts=prompts,
                    n=n_this_iter,
                    failed_approaches=tuple(state.failed_approaches),
                    reset_context=reset_context,
                )
                gen_wall_time = time.time() - gen_t0

                if not candidates:
                    self._log("[GENERATE] All candidates failed — skipping iteration")
                    log.emit(EventType.ERROR, iteration, error="all candidates failed")
                    continue

                if len(candidates) < n_this_iter:
                    failures = n_this_iter - len(candidates)
                    logger.info(
                        "Generated %d/%d candidates (%d failed)",
                        len(candidates), n_this_iter, failures,
                    )
                    self._log(f"[GENERATE] Warning: {len(candidates)}/{n_this_iter} candidates "
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
                if n_this_iter > 1:
                    self._log_candidates(verified, gen_wall_time)

                # Record all in log (use original generation-order index)
                for _sol, ver, _gen_t, _ver_t, orig_idx in verified:
                    log.emit(
                        EventType.VERIFY,
                        iteration,
                        candidate=orig_idx,
                        verdict=ver.verdict.value,
                        confidence=ver.confidence,
                        num_issues=len(ver.issues),
                    )

                # Best candidate is first (sorted by confidence desc)
                solution, verification, _, _, _ = verified[0]

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
                    log.emit(
                        EventType.ACCEPT,
                        iteration,
                        confidence=verification.confidence,
                        verdict=verification.verdict.value,
                    )
                    return self._make_result(
                        problem=problem,
                        solution=solution.solution_text,
                        verdict=Verdict.CORRECT,
                        confidence=verification.confidence,
                        iterations_used=iteration,
                        admitted_failure=False,
                        state=state,
                        log=log,
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
                        max_revisions=1 if is_reset else None,
                    )
                    if result is not None:
                        return result
                else:
                    self._log("[GENERATE] Solution unsolvable in this attempt — will retry from scratch")

                # ── UPDATE STALL TRACKING ──
                state.iteration_final_verdicts.append(verification.verdict)
                if state.best_confidence > pre_iter_best + self.config.stall_epsilon:
                    state.iterations_since_meaningful_improvement = 0
                else:
                    state.iterations_since_meaningful_improvement += 1

                # ── ACCUMULATE FAILED APPROACH ──
                summary = _summarize_failed_approach(verification)
                state.failed_approaches.append(summary)

            except anthropic.APIError as e:
                logger.warning("Iteration %d failed: %s", iteration, e)
                log.emit(EventType.ERROR, iteration, error=str(e))
                continue

        # ── FAILURE ADMISSION ──
        self._log("")
        self._log(f"{'=' * 60}")
        self._log("[ADMITTED FAILURE] Exhausted all iterations without verified solution")
        self._log(f"Best confidence seen: {state.best_confidence:.0%}")
        self._log(f"{'=' * 60}")

        log.emit(
            EventType.FAIL,
            self.config.max_iterations,
            reason="max_iterations_exhausted",
        )

        best_text = state.best_solution.solution_text if state.best_solution else None
        return self._make_result(
            problem=problem,
            solution=best_text,
            verdict=Verdict.UNSOLVED,
            confidence=state.best_confidence,
            iterations_used=self.config.max_iterations,
            admitted_failure=True,
            state=state,
            log=log,
        )

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message)
