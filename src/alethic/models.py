"""Data models for the Alethic agent."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any, ClassVar

VALID_TOOL_GUIDANCE: frozenset[str] = frozenset({"sympy", "numpy", "scipy", "matplotlib"})

MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


class Verdict(enum.Enum):
    """Verifier verdict on a candidate solution."""

    CORRECT = "correct"
    MINOR_ISSUES = "minor_issues"
    FIXABLE = "fixable"
    MAJOR_FLAW = "major_flaw"
    UNSOLVED = "unsolved"  # strategic failure admission


class IssueSeverity(enum.Enum):
    """Severity level for individual verification issues."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True)
class Issue:
    """A single issue found by the Verifier, with severity tracking."""

    text: str
    severity: IssueSeverity = IssueSeverity.MAJOR
    addressed: bool = False

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class SectionConfidence:
    """Per-section confidence from the Verifier."""

    section: str
    confidence: float
    note: str = ""


class EventType(enum.Enum):
    """Type of event in the agent's execution log."""

    GENERATE = "generate"
    VERIFY = "verify"
    REVISE = "revise"
    ERROR = "error"
    ACCEPT = "accept"
    FAIL = "fail"
    STALL_RESET = "stall_reset"


class OracleType(enum.Enum):
    """Type of verification oracle in the Verification Ladder."""

    LAYER0_STRUCTURAL = "layer0_structural"
    LAYER1_BEHAVIORAL = "layer1_behavioral"
    LAYER2_CONSISTENCY = "layer2_consistency"
    LAYER3_LLM = "layer3_llm"
    LAYER3_LLM_ADVERSARIAL = "layer3_llm_adversarial"
    LAYER4_CONSENSUS = "layer4_consensus"


@dataclass
class EvidenceState:
    """Shared evidence record accumulated across GVR loop iterations.

    Used by _compute_dynamic_n(), _check_stall(), and (future) OracleRouter.
    """

    iteration: int
    best_confidence: float
    error_category: str
    confidence_history: list[float] = field(default_factory=list)
    iteration_shape: str = "improving"   # improving | stall | oscillation | regression
    dynamic_n: int = 1
    oracle_calls_used: int = 0
    domain_check_results: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvent:
    """A single event in the agent's execution log."""

    type: EventType
    iteration: int
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenLedger:
    """Tracks cumulative token usage across API calls in a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, usage: Any) -> None:
        """Record token usage from an Anthropic API response."""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.api_calls += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "api_calls": self.api_calls,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenLedger:
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            api_calls=d.get("api_calls", 0),
        )


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
        tool_guidance: Set of tool guidance overlays to include in prompts (sympy, numpy).
        verbose: Print progress to stdout.
        stall_window: Iterations without meaningful improvement before triggering reset.
        stall_epsilon: Minimum confidence improvement to count as progress.
        stall_reset: Enable stall-triggered strategy reset.
        reset_n_boost: Additional candidates to generate on reset iterations.
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
    tool_guidance: frozenset[str] = frozenset({"sympy", "numpy"})
    verbose: bool = True
    stall_window: int = 2
    stall_epsilon: float = 0.03
    stall_reset: bool = True
    reset_n_boost: int = 1
    context_threshold: float = 0.8
    variant_b: dict[str, Any] | None = None
    adversarial_self_correction: bool = False
    adaptive_compute: bool = False          # enable N-probe at iter 1, escalate on hard problems
    adaptive_revision_budget: bool = False  # adapt max_revisions_per_cycle per iter based on category
    adaptive_budget_cap: int | None = None  # max total oracle calls; None = unlimited

    def __post_init__(self) -> None:
        if self.best_of_n < 1:
            raise ValueError(f"best_of_n must be >= 1, got {self.best_of_n}")
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.max_revisions_per_cycle < 0:
            raise ValueError(
                f"max_revisions_per_cycle must be >= 0, got {self.max_revisions_per_cycle}"
            )
        invalid = self.tool_guidance - VALID_TOOL_GUIDANCE
        if invalid:
            raise ValueError(
                f"Unknown tool_guidance values: {invalid}. "
                f"Valid values: {VALID_TOOL_GUIDANCE}"
            )
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(
                f"confidence_threshold must be in [0.0, 1.0], got {self.confidence_threshold}"
            )
        if self.temperature_generator < 0:
            raise ValueError(
                f"temperature_generator must be >= 0, got {self.temperature_generator}"
            )
        if self.temperature_verifier < 0:
            raise ValueError(f"temperature_verifier must be >= 0, got {self.temperature_verifier}")
        if self.temperature_reviser < 0:
            raise ValueError(f"temperature_reviser must be >= 0, got {self.temperature_reviser}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens}")
        if self.thinking_budget < 0:
            raise ValueError(f"thinking_budget must be >= 0, got {self.thinking_budget}")
        if self.stall_window < 1:
            raise ValueError(f"stall_window must be >= 1, got {self.stall_window}")
        if self.stall_epsilon < 0:
            raise ValueError(f"stall_epsilon must be >= 0, got {self.stall_epsilon}")
        if self.reset_n_boost < 0:
            raise ValueError(f"reset_n_boost must be >= 0, got {self.reset_n_boost}")
        if not 0.0 < self.context_threshold <= 1.0:
            raise ValueError(
                f"context_threshold must be in (0.0, 1.0], got {self.context_threshold}"
            )
        if self.adaptive_budget_cap is not None and self.adaptive_budget_cap < 1:
            raise ValueError(
                f"adaptive_budget_cap must be >= 1, got {self.adaptive_budget_cap}"
            )
        if self.variant_b is not None:
            valid_field_names = {f.name for f in dataclass_fields(self)}
            invalid_keys = set(self.variant_b) - valid_field_names
            if invalid_keys:
                raise ValueError(
                    f"variant_b contains unknown keys: {invalid_keys}. "
                    f"Valid keys: {sorted(valid_field_names)}"
                )

    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "quick": {
            "max_iterations": 2,
            "max_revisions_per_cycle": 1,
            "confidence_threshold": 0.85,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 1,
            "stall_reset": False,
            "reset_n_boost": 0,
            "context_threshold": 0.85,
        },
        "default": {
            "max_iterations": 5,
            "max_revisions_per_cycle": 3,
            "confidence_threshold": 0.90,
            "extended_thinking": False,
            "max_tokens": 16384,
            "best_of_n": 2,
            "stall_window": 2,
            "stall_epsilon": 0.03,
            "stall_reset": True,
            "reset_n_boost": 1,
            "context_threshold": 0.8,
            "adaptive_revision_budget": True,
        },
        "thorough": {
            "max_iterations": 8,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.95,
            "extended_thinking": True,
            "thinking_budget": 15000,
            "max_tokens": 32768,
            "best_of_n": 3,
            "stall_window": 3,
            "stall_epsilon": 0.02,
            "stall_reset": True,
            "reset_n_boost": 1,
            "context_threshold": 0.8,
            "variant_b": {"model": "claude-sonnet-4-6"},
            "adversarial_self_correction": True,
            "adaptive_compute": True,
        },
        "extreme": {
            "max_iterations": 12,
            "max_revisions_per_cycle": 5,
            "confidence_threshold": 0.97,
            "extended_thinking": True,
            "thinking_budget": 40000,
            "max_tokens": 65536,
            "best_of_n": 5,
            "stall_window": 3,
            "stall_epsilon": 0.02,
            "stall_reset": True,
            "reset_n_boost": 2,
            "context_threshold": 0.75,
            "variant_b": {"model": "claude-sonnet-4-6"},
            "adversarial_self_correction": True,
            "adaptive_compute": True,
        },
    }

    def build_variant_b_config(self) -> AgentConfig:
        """Build a new AgentConfig with variant_b overrides applied.

        Returns a new AgentConfig where fields from self.variant_b override
        the current config's values. The returned config has variant_b=None
        to avoid recursion.

        Raises:
            ValueError: If variant_b is None.
        """
        if self.variant_b is None:
            raise ValueError("variant_b is None; cannot build variant B config")
        base = {f.name: getattr(self, f.name) for f in dataclass_fields(self)}
        base.update(self.variant_b)
        base["variant_b"] = None
        return AgentConfig(**base)

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
                f"Unknown preset '{name}'. Available presets: {', '.join(cls.PRESETS)}"
            )
        params = dict(cls.PRESETS[name])
        params.update(overrides)
        return cls(**params)


@dataclass(frozen=True)
class VerifierConfig:
    """Configuration for standalone verify and check commands.

    Controls multi-verifier consensus: K independent verifiers run in parallel,
    results are mechanically aggregated, then a lightweight LLM pass cleans up
    the merged critique.
    """

    model: str = "claude-opus-4-6"
    num_verifiers: int = 3
    tool_guidance: frozenset[str] = frozenset({"sympy", "numpy", "scipy", "matplotlib"})
    domain: str | None = None  # None = auto-detect
    enable_code_execution: bool = True
    temperature: float = 0.2
    max_tokens: int = 16384
    extended_thinking: bool = False
    thinking_budget: int = 15000
    verbose: bool = True
    verification_ladder: bool = True  # inject verification-ladder.md into each K verifier

    def __post_init__(self) -> None:
        if self.num_verifiers < 1:
            raise ValueError(f"num_verifiers must be >= 1, got {self.num_verifiers}")
        invalid = self.tool_guidance - VALID_TOOL_GUIDANCE
        if invalid:
            raise ValueError(
                f"Unknown tool_guidance values: {invalid}. "
                f"Valid: {VALID_TOOL_GUIDANCE}"
            )
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be >= 1, got {self.max_tokens}")
        if self.temperature < 0:
            raise ValueError(f"temperature must be >= 0, got {self.temperature}")
        if self.thinking_budget < 0:
            raise ValueError(f"thinking_budget must be >= 0, got {self.thinking_budget}")

    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "quick": {"num_verifiers": 2, "extended_thinking": False, "max_tokens": 16384},
        "default": {"num_verifiers": 3, "extended_thinking": False, "max_tokens": 16384},
        "thorough": {
            "num_verifiers": 5,
            "extended_thinking": True,
            "thinking_budget": 15000,
            "max_tokens": 32768,
        },
        "extreme": {
            "num_verifiers": 7,
            "extended_thinking": True,
            "thinking_budget": 40000,
            "max_tokens": 65536,
        },
    }

    @classmethod
    def from_preset(cls, name: str, **overrides: Any) -> VerifierConfig:
        """Create a VerifierConfig from a named preset with optional overrides.

        Args:
            name: Preset name (quick, default, thorough, extreme).
            **overrides: Field values that override the preset.

        Returns:
            VerifierConfig with preset values, overridden by any explicit kwargs.

        Raises:
            ValueError: If the preset name is unknown.
        """
        if name not in cls.PRESETS:
            raise ValueError(
                f"Unknown preset '{name}'. Available presets: {', '.join(cls.PRESETS)}"
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
    issues: list[Issue] = field(default_factory=list)
    reason: str = ""  # For false-premise detection (REASON field from verifier)
    section_confidences: list[SectionConfidence] = field(default_factory=list)
    corrected_solution: str | None = None  # For FIXABLE verdicts

    def is_acceptable(self, threshold: float = 0.90) -> bool:
        has_critical = any(issue.severity == IssueSeverity.CRITICAL for issue in self.issues)
        return self.verdict == Verdict.CORRECT and self.confidence >= threshold and not has_critical

    def needs_revision(self, threshold: float = 0.90) -> bool:
        return self.verdict in (Verdict.MINOR_ISSUES, Verdict.FIXABLE, Verdict.MAJOR_FLAW) or (
            self.verdict == Verdict.CORRECT and self.confidence < threshold
        )

    @property
    def has_correction(self) -> bool:
        """True if this is a FIXABLE verdict with a verifier-generated corrected solution."""
        return self.verdict == Verdict.FIXABLE and self.corrected_solution is not None

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


@dataclass(frozen=True)
class ConsensusIssue:
    """An issue flagged by one or more independent verifiers."""

    text: str
    severity: IssueSeverity = IssueSeverity.MAJOR
    flagged_by: int = 1  # how many of K verifiers flagged this


@dataclass
class ConsensusResult:
    """Synthesized result from K independent verifications."""

    verdict: Verdict
    confidence: float
    confidence_range: tuple[float, float]
    critique: str
    issues: list[ConsensusIssue]
    individual_results: list[VerificationResult]
    domain_detected: str
    num_verifiers: int
    elapsed_seconds: float = 0.0

    @property
    def consensus_ratio(self) -> str:
        """E.g. '3/3' or '2/3' -- how many verifiers agree with the majority verdict."""
        if not self.individual_results:
            return "0/0"
        agree = sum(1 for r in self.individual_results if r.verdict == self.verdict)
        return f"{agree}/{len(self.individual_results)}"

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict representation."""
        return {
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "confidence_range": list(self.confidence_range),
            "consensus_ratio": self.consensus_ratio,
            "critique": self.critique,
            "issues": [
                {
                    "text": i.text,
                    "severity": i.severity.value,
                    "flagged_by": i.flagged_by,
                }
                for i in self.issues
            ],
            "domain_detected": self.domain_detected,
            "num_verifiers": self.num_verifiers,
            "elapsed_seconds": self.elapsed_seconds,
            "individual_results": [
                {
                    "verdict": r.verdict.value,
                    "confidence": r.confidence,
                    "critique": r.critique,
                }
                for r in self.individual_results
            ],
        }


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
    events: list[AgentEvent] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    candidates_per_iteration: int = 1
    failed_approaches: list[str] = field(default_factory=list)
    token_ledger: TokenLedger | None = None
    session_dir: str | None = None
    checkpoint_path: str | None = None

    @property
    def solved(self) -> bool:
        return self.verdict == Verdict.CORRECT and self.solution is not None

    @property
    def history(self) -> list[dict]:
        """Backward-compatible dict view of events. Deprecated: use .events instead."""
        import warnings

        warnings.warn(
            "AgentResult.history is deprecated; use AgentResult.events instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return [{"phase": e.type.value, "iteration": e.iteration, **e.data} for e in self.events]

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
        if self.failed_approaches:
            lines.append(f"Failed approaches: {len(self.failed_approaches)}")
        lines.extend(
            [
                f"Time: {self.elapsed_seconds:.1f}s",
                f"{'=' * 60}",
            ]
        )
        if self.solution:
            lines.append("")
            lines.append(self.solution)
        elif self.admitted_failure:
            lines.append("")
            lines.append("[Agent admitted failure — problem could not be solved reliably]")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict representation."""
        d: dict = {
            "problem": self.problem,
            "solved": self.solved,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "iterations_used": self.iterations_used,
            "total_revisions": self.total_revisions,
            "candidates_per_iteration": self.candidates_per_iteration,
            "admitted_failure": self.admitted_failure,
            "elapsed_seconds": self.elapsed_seconds,
            "solution": self.solution,
            "failed_approaches": self.failed_approaches,
            "events": [
                {
                    "type": e.type.value,
                    "iteration": e.iteration,
                    "timestamp": e.timestamp,
                    **e.data,
                }
                for e in self.events
            ],
        }
        if self.token_ledger:
            d["token_usage"] = self.token_ledger.to_dict()
        if self.session_dir:
            d["session_dir"] = self.session_dir
        if self.checkpoint_path:
            d["checkpoint_path"] = self.checkpoint_path
        return d
