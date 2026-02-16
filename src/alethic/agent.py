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

import anthropic

from alethic.models import AgentConfig, AgentResult, Solution, Verdict, VerificationResult
from alethic.subagents import generate, revise, verify

logger = logging.getLogger("alethic")

_EMPTY_REASONS = frozenset({"n/a", "na", "none", "not applicable", ""})


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
        total_revisions: int,
        history: list[dict],
        start_time: float,
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
            elapsed = time.time() - start_time
            return AgentResult(
                problem=problem,
                solution=verification.reason,
                verdict=Verdict.UNSOLVED,
                confidence=verification.confidence,
                iterations_used=iteration,
                total_revisions=total_revisions,
                admitted_failure=False,
                history=history,
                elapsed_seconds=elapsed,
            )
        return None

    def _generate_candidates(
        self,
        *,
        problem: str,
        config: AgentConfig,
        iteration: int,
        balanced: bool,
        prompts: dict[str, str],
        n: int,
    ) -> list[tuple[Solution, float]]:
        """Generate N candidates. Parallel (ThreadPoolExecutor) when N>1, sequential when N=1."""

        def _gen_one(candidate_idx: int) -> tuple[Solution, float]:
            t0 = time.time()
            sol = generate(
                self.client,
                problem=problem,
                config=config,
                iteration=iteration,
                balanced=balanced,
                system_prompt=prompts.get("generator_system"),
                user_template=prompts.get("generator_user"),
                balanced_addendum=prompts.get("balanced_addendum"),
            )
            return sol, time.time() - t0

        if n == 1:
            return [_gen_one(1)]

        results: list[tuple[Solution, float]] = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {pool.submit(_gen_one, i): i for i in range(1, n + 1)}
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
        total_revisions: int,
        history: list[dict],
        start_time: float,
        threshold: float,
        best_solution: Solution | None,
        best_confidence: float,
    ) -> AgentResult | tuple[int, Solution | None, float]:
        """Run revision sub-loop. Returns AgentResult if solved, else updated state tuple."""
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
            total_revisions += 1
            history.append({
                "phase": "revise",
                "iteration": iteration,
                "revision": rev_num,
            })

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
            history.append({
                "phase": "verify",
                "iteration": iteration,
                "revision": rev_num,
                "verdict": verification.verdict.value,
                "confidence": verification.confidence,
            })
            self._log(f"[VERIFY] Verdict: {verification.verdict.value} "
                       f"(confidence: {verification.confidence:.0%})")

            if verification.confidence > best_confidence:
                best_confidence = verification.confidence
                best_solution = current_solution

            if verification.is_acceptable(threshold):
                self._log("")
                self._log("[SOLVED] Verifier approved the revised solution!")
                elapsed = time.time() - start_time
                return AgentResult(
                    problem=problem,
                    solution=current_solution.solution_text,
                    verdict=Verdict.CORRECT,
                    confidence=verification.confidence,
                    iterations_used=iteration,
                    total_revisions=total_revisions,
                    admitted_failure=False,
                    history=history,
                    elapsed_seconds=elapsed,
                )

            # If major flaw after revision, break to restart from generator
            if verification.verdict == Verdict.MAJOR_FLAW:
                self._log("[REVISE] Major flaw persists — restarting from generator")
                break

            # If unsolved after revision, check false premise then restart
            if verification.verdict == Verdict.UNSOLVED:
                fp = self._check_false_premise(
                    verification, problem, iteration,
                    total_revisions, history, start_time,
                )
                if fp:
                    return fp
                self._log("[REVISE] Solution unsolvable — restarting from generator")
                break
        else:
            self._log(f"[REVISE] Exhausted revision attempts for iteration {iteration}")

        return total_revisions, best_solution, best_confidence

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
        start_time = time.time()
        history: list[dict] = []
        total_revisions = 0
        best_solution = None
        best_confidence = 0.0
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
                # ── GENERATE N CANDIDATES ──
                if n > 1:
                    self._log(f"[GENERATE] Producing {n} candidate solutions (parallel)...")
                else:
                    self._log("[GENERATE] Producing candidate solution...")

                gen_t0 = time.time()
                candidates = self._generate_candidates(
                    problem=problem,
                    config=self.config,
                    iteration=iteration,
                    balanced=balanced,
                    prompts=prompts,
                    n=n,
                )
                gen_wall_time = time.time() - gen_t0

                if not candidates:
                    self._log("[GENERATE] All candidates failed — skipping iteration")
                    history.append({"phase": "error", "iteration": iteration, "error": "all candidates failed"})
                    continue

                for idx, (sol, gen_t) in enumerate(candidates, 1):
                    history.append({
                        "phase": "generate",
                        "iteration": iteration,
                        "candidate": idx,
                        "solution_preview": sol.solution_text[:500],
                    })
                    self._log(f"[GENERATE] Candidate {idx}/{len(candidates)} produced "
                               f"({len(sol.solution_text)} chars, {gen_t:.1f}s)")

                # ── VERIFY ALL CANDIDATES (decoupled) ──
                self._log("[VERIFY] Independently verifying candidates...")
                verified = self._verify_candidates(
                    problem=problem,
                    candidates=candidates,
                    prompts=prompts,
                )

                # Log rankings for N>1
                if n > 1:
                    self._log_candidates(verified, gen_wall_time)

                # Record all in history
                for idx, (_sol, ver, _gen_t, _ver_t) in enumerate(verified, 1):
                    history.append({
                        "phase": "verify",
                        "iteration": iteration,
                        "candidate": idx,
                        "verdict": ver.verdict.value,
                        "confidence": ver.confidence,
                        "num_issues": len(ver.issues),
                    })

                # Best candidate is first (sorted by confidence desc)
                solution, verification, _, _ = verified[0]

                self._log(f"[VERIFY] Best: {verification.verdict.value} "
                           f"(confidence: {verification.confidence:.0%})")
                if verification.issues:
                    for issue in verification.issues:
                        self._log(f"  Issue: {issue[:100]}")

                # Track best solution seen
                if verification.confidence > best_confidence:
                    best_confidence = verification.confidence
                    best_solution = solution

                # ── CHECK: Is it correct? ──
                if verification.is_acceptable(threshold):
                    self._log("")
                    self._log("[SOLVED] Verifier approved the solution!")
                    elapsed = time.time() - start_time
                    return AgentResult(
                        problem=problem,
                        solution=solution.solution_text,
                        verdict=Verdict.CORRECT,
                        confidence=verification.confidence,
                        iterations_used=iteration,
                        total_revisions=total_revisions,
                        admitted_failure=False,
                        history=history,
                        elapsed_seconds=elapsed,
                        candidates_per_iteration=n,
                    )

                # ── CHECK: False premise? ──
                fp = self._check_false_premise(
                    verification, problem, iteration,
                    total_revisions, history, start_time,
                )
                if fp:
                    fp.candidates_per_iteration = n
                    return fp

                # ── REVISE (best candidate only, if fixable) ──
                if verification.needs_revision(threshold):
                    result = self._run_revision_loop(
                        problem=problem,
                        solution=solution,
                        verification=verification,
                        prompts=prompts,
                        iteration=iteration,
                        total_revisions=total_revisions,
                        history=history,
                        start_time=start_time,
                        threshold=threshold,
                        best_solution=best_solution,
                        best_confidence=best_confidence,
                    )
                    if isinstance(result, AgentResult):
                        result.candidates_per_iteration = n
                        return result
                    total_revisions, best_solution, best_confidence = result
                else:
                    self._log("[GENERATE] Solution unsolvable in this attempt — will retry from scratch")

            except anthropic.APIError as e:
                logger.warning("Iteration %d failed: %s", iteration, e)
                history.append({"phase": "error", "iteration": iteration, "error": str(e)})
                continue

        # ── FAILURE ADMISSION ──
        elapsed = time.time() - start_time
        self._log("")
        self._log(f"{'=' * 60}")
        self._log("[ADMITTED FAILURE] Exhausted all iterations without verified solution")
        self._log(f"Best confidence seen: {best_confidence:.0%}")
        self._log(f"{'=' * 60}")

        return AgentResult(
            problem=problem,
            solution=best_solution.solution_text if best_solution else None,
            verdict=Verdict.UNSOLVED,
            confidence=best_confidence,
            iterations_used=self.config.max_iterations,
            total_revisions=total_revisions,
            admitted_failure=True,
            history=history,
            elapsed_seconds=elapsed,
            candidates_per_iteration=n,
        )

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message)
