"""Standalone verification agents with multi-verifier consensus.

VerifierAgent: problem + solution -> ConsensusResult
CheckerAgent: solution only -> ConsensusResult (internal consistency)
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from alethic.check_prompts import CHECKER_SYSTEM, CHECKER_USER
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
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

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
            tool_guidance=frozenset(t for t in self.config.tool_guidance if t in {"sympy", "numpy"}),
            verbose=False,
        )

    def _run_single_verify(
        self, problem: str, solution_text: str, system: str, user_template: str
    ) -> VerificationResult:
        """Run one independent verification."""
        agent_config = self._build_agent_config()
        sol = Solution(problem=problem, solution_text=solution_text, iteration=0)
        return verify_subagent(
            self.client,
            problem=problem,
            solution=sol,
            config=agent_config,
            system_prompt=system,
            user_template=user_template,
        )

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        """Verify a solution against a stated problem with K-verifier consensus."""
        start = time.time()
        domain = detect_domain(f"{problem}\n{solution}", override=self.config.domain)
        system, user_template = self._select_prompts(domain)
        k = self.config.num_verifiers

        if self.config.verbose:
            print(f"Running {k} independent verifiers (domain: {domain})...")

        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=k) as executor:
            futures = [
                executor.submit(self._run_single_verify, problem, solution, system, user_template)
                for _ in range(k)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return self._synthesize(results, domain, start)

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency of a solution (no problem statement)."""
        raise NotImplementedError("Use CheckerAgent for check()")

    def _synthesize(
        self, results: list[VerificationResult], domain: str, start: float
    ) -> ConsensusResult:
        """Mechanical aggregation + LLM critique cleanup."""
        aggregation = aggregate_mechanical(results)

        if self.config.verbose:
            print(f"Aggregated: {aggregation['verdict'].value} ({aggregation['confidence']:.2f})")
            print("Synthesizing critique...")

        critique = synthesize_critique(
            self.client, results, aggregation, model=self.config.model
        )

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


class CheckerAgent(VerifierAgent):
    """Internal consistency checker — solution only, no problem statement."""

    def _select_prompts(self, domain: str) -> tuple[str, str]:
        return CHECKER_SYSTEM, CHECKER_USER

    def check(self, solution: str) -> ConsensusResult:
        """Check internal consistency with K-verifier consensus."""
        start = time.time()
        domain = detect_domain(solution, override=self.config.domain)
        system, user_template = self._select_prompts(domain)
        k = self.config.num_verifiers

        if self.config.verbose:
            print(f"Running {k} independent checkers (domain: {domain})...")

        results: list[VerificationResult] = []
        with ThreadPoolExecutor(max_workers=k) as executor:
            futures = [
                executor.submit(self._run_single_verify, "", solution, system, user_template)
                for _ in range(k)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return self._synthesize(results, domain, start)

    def verify(self, problem: str, solution: str) -> ConsensusResult:
        """Not supported — use VerifierAgent for verify()."""
        raise NotImplementedError("Use VerifierAgent for verify()")
