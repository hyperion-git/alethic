"""Main Alethic agent orchestrator.

Implements the Generate → Verify → Revise loop inspired by Google DeepMind's
Aletheia architecture. The loop continues until:
  1. The Verifier approves the solution (verdict = CORRECT), or
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

import anthropic

from alethic.models import AgentConfig, AgentResult, Verdict
from alethic.subagents import generate, revise, verify

logger = logging.getLogger("alethic")


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

        self._log(f"{'=' * 60}")
        self._log("ALETHIC MATH AGENT")
        self._log(f"Model: {self.config.model}")
        self._log(f"Max iterations: {self.config.max_iterations}")
        self._log(f"Confidence threshold: {threshold:.0%}")
        self._log(f"Code execution: {'enabled' if self.config.enable_code_execution else 'disabled'}")
        self._log(f"{'=' * 60}")
        self._log(f"Problem: {problem[:200]}{'...' if len(problem) > 200 else ''}")
        self._log("")

        for iteration in range(1, self.config.max_iterations + 1):
            self._log(f"{'─' * 40}")
            self._log(f"Iteration {iteration}/{self.config.max_iterations}")
            self._log(f"{'─' * 40}")

            # ── GENERATE ──
            self._log("[GENERATE] Producing candidate solution...")
            solution = generate(
                self.client,
                problem=problem,
                config=self.config,
                iteration=iteration,
                balanced=balanced,
            )
            history.append({
                "phase": "generate",
                "iteration": iteration,
                "solution_preview": solution.solution_text[:500],
            })
            self._log(f"[GENERATE] Solution produced ({len(solution.solution_text)} chars)")

            # ── VERIFY (decoupled — no access to generator's thinking) ──
            self._log("[VERIFY] Independently verifying solution...")
            verification = verify(
                self.client,
                problem=problem,
                solution=solution,
                config=self.config,
            )
            history.append({
                "phase": "verify",
                "iteration": iteration,
                "verdict": verification.verdict.value,
                "confidence": verification.confidence,
                "num_issues": len(verification.issues),
            })
            self._log(f"[VERIFY] Verdict: {verification.verdict.value} "
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
                )

            # ── CHECK: False premise? ──
            _empty_reasons = {"n/a", "na", "none", "not applicable", ""}
            if verification.verdict == Verdict.UNSOLVED and verification.reason and verification.reason.strip().lower() not in _empty_reasons:
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

            # ── REVISE (if fixable) ──
            if verification.needs_revision(threshold):
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
                        _empty_reasons = {"n/a", "na", "none", "not applicable", ""}
                        if verification.reason and verification.reason.strip().lower() not in _empty_reasons:
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
                        self._log("[REVISE] Solution unsolvable — restarting from generator")
                        break

                self._log(f"[REVISE] Exhausted revision attempts for iteration {iteration}")
            else:
                self._log("[GENERATE] Solution unsolvable in this attempt — will retry from scratch")

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
        )

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message)
