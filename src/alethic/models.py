"""Data models for the Alethic agent."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar


class Verdict(enum.Enum):
    """Verifier verdict on a candidate solution."""

    CORRECT = "correct"
    MINOR_ISSUES = "minor_issues"
    MAJOR_FLAW = "major_flaw"
    UNSOLVED = "unsolved"  # strategic failure admission


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for the Alethic agent.

    Attributes:
        model: Anthropic model ID.
        max_iterations: Max generate-verify-revise cycles before giving up.
        max_revisions_per_cycle: Max revisions within a single cycle before
            restarting from the generator.
        enable_code_execution: Allow Python sandbox for computational verification.
        temperature_generator: Sampling temperature for the generator.
        temperature_verifier: Sampling temperature for the verifier (lower = stricter).
        temperature_reviser: Sampling temperature for the reviser.
        max_tokens: Max tokens per API call.
        extended_thinking: Enable Claude's extended thinking mode.
        thinking_budget: Token budget for extended thinking.
        confidence_threshold: Minimum confidence for accepting a solution.
        best_of_n: Number of candidates to generate per iteration (1 = sequential).
        verbose: Print progress to stdout.
    """

    model: str = "claude-opus-4-6"
    max_iterations: int = 5
    max_revisions_per_cycle: int = 3
    enable_code_execution: bool = True
    temperature_generator: float = 1.0
    temperature_verifier: float = 0.2
    temperature_reviser: float = 0.7
    max_tokens: int = 16384
    extended_thinking: bool = False
    thinking_budget: int = 10000
    confidence_threshold: float = 0.90
    best_of_n: int = 1
    verbose: bool = True

    def __post_init__(self) -> None:
        if self.best_of_n < 1:
            raise ValueError(f"best_of_n must be >= 1, got {self.best_of_n}")

    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "quick": {
            "max_iterations": 2,
            "max_revisions_per_cycle": 1,
            "confidence_threshold": 0.85,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 1,
        },
        "default": {
            "max_iterations": 5,
            "max_revisions_per_cycle": 3,
            "confidence_threshold": 0.90,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 2,
        },
        "thorough": {
            "max_iterations": 8,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.95,
            "extended_thinking": True,
            "thinking_budget": 15000,
            "max_tokens": 32768,
            "best_of_n": 3,
        },
        "extreme": {
            "max_iterations": 12,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.97,
            "extended_thinking": True,
            "thinking_budget": 40000,
            "max_tokens": 65536,
            "best_of_n": 5,
        },
    }

    @classmethod
    def from_preset(cls, name: str, **overrides: Any) -> AgentConfig:
        """Create an AgentConfig from a named preset with optional overrides.

        Args:
            name: Preset name (quick, default, thorough, extreme).
            **overrides: Field values that override the preset.

        Returns:
            AgentConfig with preset values, overridden by any explicit kwargs.

        Raises:
            ValueError: If the preset name is unknown.
        """
        if name not in cls.PRESETS:
            raise ValueError(
                f"Unknown preset '{name}'. "
                f"Available presets: {', '.join(cls.PRESETS)}"
            )
        params = dict(cls.PRESETS[name])
        params.update(overrides)
        return cls(**params)


@dataclass
class Solution:
    """A candidate solution produced by the Generator."""

    problem: str
    solution_text: str
    iteration: int
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return self.solution_text


@dataclass
class VerificationResult:
    """Result from the Verifier subagent."""

    verdict: Verdict
    critique: str
    confidence: float  # 0.0 to 1.0
    issues: list[str] = field(default_factory=list)
    reason: str = ""  # For false-premise detection (REASON field from verifier)

    def is_acceptable(self, threshold: float = 0.90) -> bool:
        return self.verdict == Verdict.CORRECT and self.confidence >= threshold

    def needs_revision(self, threshold: float = 0.90) -> bool:
        return self.verdict in (Verdict.MINOR_ISSUES, Verdict.MAJOR_FLAW) or (
            self.verdict == Verdict.CORRECT and self.confidence < threshold
        )

    def __str__(self) -> str:
        lines = [
            f"Verdict: {self.verdict.value}",
            f"Confidence: {self.confidence:.0%}",
            f"Critique: {self.critique}",
        ]
        if self.issues:
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
        return "\n".join(lines)


@dataclass
class Revision:
    """A revision produced by the Reviser subagent."""

    revised_solution: str
    changes_made: str
    revision_number: int
    based_on_critique: str


@dataclass
class AgentResult:
    """Final result from the Alethic agent's solve() method."""

    problem: str
    solution: str | None
    verdict: Verdict
    confidence: float
    iterations_used: int
    total_revisions: int
    admitted_failure: bool
    history: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    candidates_per_iteration: int = 1

    @property
    def solved(self) -> bool:
        return self.verdict == Verdict.CORRECT and self.solution is not None

    def __str__(self) -> str:
        status = "SOLVED" if self.solved else "UNSOLVED"
        lines = [
            f"{'=' * 60}",
            f"Result: {status}",
            f"Confidence: {self.confidence:.0%}",
            f"Iterations: {self.iterations_used}",
            f"Total revisions: {self.total_revisions}",
        ]
        if self.candidates_per_iteration > 1:
            lines.append(f"Candidates per iteration: {self.candidates_per_iteration}")
        lines.extend([
            f"Time: {self.elapsed_seconds:.1f}s",
            f"{'=' * 60}",
        ])
        if self.solution:
            lines.append("")
            lines.append(self.solution)
        elif self.admitted_failure:
            lines.append("")
            lines.append("[Agent admitted failure — problem could not be solved reliably]")
        return "\n".join(lines)
