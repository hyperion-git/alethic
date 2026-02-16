"""Physics derivation agent — thin subclass of MathAgent.

Swaps in physics-specific prompt templates while reusing the entire
Generate → Verify → Revise orchestrator loop.
"""

from __future__ import annotations

import logging
import time

from alethic.agent import MathAgent
from alethic.models import AgentResult, Verdict
from alethic.physics_prompts import (
    BALANCED_PHYSICS_ADDENDUM,
    PHYSICS_GENERATOR_SYSTEM,
    PHYSICS_GENERATOR_USER,
    PHYSICS_REVISER_SYSTEM,
    PHYSICS_REVISER_USER,
    PHYSICS_VERIFIER_SYSTEM,
    PHYSICS_VERIFIER_USER,
)
from alethic.subagents import generate, revise, verify

logger = logging.getLogger("alethic")


class PhysicsAgent(MathAgent):
    """Alethic physics derivation agent powered by Claude.

    Thin subclass of MathAgent that injects physics-specific prompt templates
    into the Generate → Verify → Revise loop. All orchestrator logic is
    inherited from MathAgent — only the prompts differ.

    Usage:
        agent = PhysicsAgent()  # uses ANTHROPIC_API_KEY
        result = agent.solve("Derive the energy spectrum of the quantum harmonic oscillator.")
        print(result)
    """

    def solve(
        self,
        problem: str,
        balanced: bool = True,
    ) -> AgentResult:
        """Solve a physics derivation problem using the Generate → Verify → Revise loop.

        Args:
            problem: The physics problem statement.
            balanced: Use balanced prompting (check limiting cases first).

        Returns:
            AgentResult with the derivation (or admitted failure).
        """
        start_time = time.time()
        history: list[dict] = []
        total_revisions = 0
        best_solution = None
        best_confidence = 0.0
        threshold = self.config.confidence_threshold

        self._log(f"{'=' * 60}")
        self._log("ALETHIC PHYSICS DERIVATION AGENT")
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
            self._log("[GENERATE] Producing candidate derivation...")
            solution = generate(
                self.client,
                problem=problem,
                config=self.config,
                iteration=iteration,
                balanced=balanced,
                system_prompt=PHYSICS_GENERATOR_SYSTEM,
                user_template=PHYSICS_GENERATOR_USER,
                balanced_addendum=BALANCED_PHYSICS_ADDENDUM,
            )
            history.append({
                "phase": "generate",
                "iteration": iteration,
                "solution_preview": solution.solution_text[:500],
            })
            self._log(f"[GENERATE] Derivation produced ({len(solution.solution_text)} chars)")

            # ── VERIFY (decoupled — no access to generator's thinking) ──
            self._log("[VERIFY] Independently verifying derivation...")
            verification = verify(
                self.client,
                problem=problem,
                solution=solution,
                config=self.config,
                system_prompt=PHYSICS_VERIFIER_SYSTEM,
                user_template=PHYSICS_VERIFIER_USER,
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
                self._log("[SOLVED] Verifier approved the derivation!")
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
                        system_prompt=PHYSICS_REVISER_SYSTEM,
                        user_template=PHYSICS_REVISER_USER,
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
                        system_prompt=PHYSICS_VERIFIER_SYSTEM,
                        user_template=PHYSICS_VERIFIER_USER,
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
                        self._log("[SOLVED] Verifier approved the revised derivation!")
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
                        self._log("[REVISE] Derivation unsolvable — restarting from generator")
                        break

                self._log(f"[REVISE] Exhausted revision attempts for iteration {iteration}")
            else:
                self._log("[GENERATE] Derivation unsolvable in this attempt — will retry from scratch")

        # ── FAILURE ADMISSION ──
        elapsed = time.time() - start_time
        self._log("")
        self._log(f"{'=' * 60}")
        self._log("[ADMITTED FAILURE] Exhausted all iterations without verified derivation")
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
