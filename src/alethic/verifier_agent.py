"""Standalone verification agents with multi-verifier consensus.

VerifierAgent: problem + solution -> ConsensusResult
CheckerAgent: solution only -> ConsensusResult (internal consistency)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from alethic.check_prompts import CHECKER_SYSTEM, CHECKER_USER
from alethic.client_factory import get_client
from alethic.domain import detect_domain
from alethic.models import (
    AgentConfig,
    ConsensusResult,
    Solution,
    VerificationResult,
    VerifierConfig,
)
from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM, PHYSICS_VERIFIER_USER
from alethic.prompts import VERIFIER_SYSTEM, VERIFIER_USER
from alethic.subagents import verify as verify_subagent
from alethic.synthesizer import aggregate_mechanical, synthesize_critique

logger = logging.getLogger("alethic")


class VerifierAgent:
    """Runs K independent verifications and synthesizes a consensus."""

    def __init__(self, config: VerifierConfig | None = None, *, api_key: str | None = None):
        self.config = config or VerifierConfig()
        self.client = get_client(api_key=api_key)

    def _select_prompts(self, domain: str) -> tuple[str, str]:
        """Return (system_prompt, user_template) for the detected domain."""
        if domain == "physics":
            return PHYSICS_VERIFIER_SYSTEM, PHYSICS_VERIFIER_USER
        return VERIFIER_SYSTEM, VERIFIER_USER

    def _build_agent_config(self) -> AgentConfig:
        """Adapt VerifierConfig to AgentConfig for the verify() subagent."""
        return AgentConfig(
            model=self.config.model,
            enable_code_execution=self.config.enable_code_execution,
            temperature_verifier=self.config.temperature,
            max_tokens=self.config.max_tokens,
            extended_thinking=self.config.extended_thinking,
            thinking_budget=self.config.thinking_budget,
            tool_guidance=self.config.tool_guidance,
            verbose=False,
        )

    _LADDER_PROMPT = (
        "\n\n## VERIFICATION LADDER REQUIRED\n\n"
        "Before forming your verdict, execute structured Layer 0-2 checks using your "
        "Python sandbox. Embed outputs as `[Layer N check]: {result}`. "
        "A Layer 0 failure is immediately [MAJOR] regardless of the algebra quality. "
        "See instructions embedded in the verification task."
    )

    def _run_consensus(
        self, problem: str, solution: str, detection_text: str, label: str
    ) -> ConsensusResult:
        """Shared pipeline: detect domain, run K verifiers in parallel, synthesize.

        Args:
            problem: Problem statement (empty string for check mode).
            solution: Solution text to verify/check.
            detection_text: Text passed to domain auto-detection.
            label: Human-readable label for verbose output ("verifiers" or "checkers").
        """
        start = time.time()
        domain = detect_domain(detection_text, override=self.config.domain)
        system, user_template = self._select_prompts(domain)
        k = self.config.num_verifiers
        agent_config = self._build_agent_config()
        sol = Solution(problem=problem, solution_text=solution, iteration=0)
        extra_system = self._LADDER_PROMPT if self.config.verification_ladder else None

        if self.config.verbose:
            print(f"Running {k} independent {label} (domain: {domain})...")

        def run_one() -> VerificationResult:
            return verify_subagent(
                self.client,
                problem=problem,
                solution=sol,
                config=agent_config,
                system_prompt=system,
                user_template=user_template,
                extra_system=extra_system,
            )

        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=k) as executor:
            futures = [executor.submit(run_one) for _ in range(k)]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning("Verifier %d/%d failed: %s", len(results) + 1, k, e)
        if not results:
            raise RuntimeError(f"All {k} verifiers failed")

        aggregation = aggregate_mechanical(results)

        if self.config.verbose:
            print(f"Aggregated: {aggregation['verdict'].value} ({aggregation['confidence']:.2f})")
            print("Synthesizing critique...")

        critique = synthesize_critique(self.client, results, aggregation, model=self.config.model)

        return ConsensusResult(
            verdict=aggregation["verdict"],
            confidence=aggregation["confidence"],
            confidence_range=aggregation["confidence_range"],
            critique=critique,
            issues=aggregation["issues"],
            individual_results=results,
            domain_detected=domain,
            num_verifiers=len(results),
            elapsed_seconds=time.time() - start,
        )

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        """Verify a solution against a stated problem with K-verifier consensus."""
        return self._run_consensus(
            problem=problem,
            solution=solution,
            detection_text=f"{problem}\n{solution}",
            label="verifiers",
        )

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency of a solution (no problem statement)."""
        raise NotImplementedError("Use CheckerAgent for check()")


class CheckerAgent(VerifierAgent):
    """Internal consistency checker — solution only, no problem statement."""

    def _select_prompts(self, domain: str) -> tuple[str, str]:
        return CHECKER_SYSTEM, CHECKER_USER

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency with K-verifier consensus."""
        return self._run_consensus(
            problem="",
            solution=solution,
            detection_text=solution,
            label="checkers",
        )

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        """Not supported — use VerifierAgent for verify()."""
        raise NotImplementedError("Use VerifierAgent for verify()")
