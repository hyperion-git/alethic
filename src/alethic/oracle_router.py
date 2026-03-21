"""OracleRouter — pure-function routing layer for the GVR loop.

Consolidates ~10 routing functions from agent.py into a single class.
Reads RunState + EvidenceState, produces RoutingDecision. Never mutates state.

Created in v3.7 as a strangler fig refactor. The agent.py main loop becomes
a consumer of routing decisions rather than a producer of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from alethic.atoms import (
    AtomAnnotation,
    AtomStability,
    classify_atom_stability,
    parse_layer_results,
)
from alethic.error_taxonomy import _ORACLE_ROUTING
from alethic.models import (
    AgentConfig,
    EvidenceState,
    OracleType,
    Verdict,
    VerificationResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable


# --- Constants (moved from agent.py) ---

_HIGH_ORACLE_TYPES = frozenset({
    OracleType.LAYER3_LLM,
    OracleType.LAYER3_LLM_ADVERSARIAL,
    OracleType.LAYER4_CONSENSUS,
})
_LAYER_BY_ORACLE = {
    OracleType.LAYER0_STRUCTURAL: 0,
    OracleType.LAYER1_BEHAVIORAL: 1,
    OracleType.LAYER2_CONSISTENCY: 2,
}
_SENTINEL_FAILURE_MARKERS = ("FAILED", "Traceback")


# --- RoutingDecision ---

@dataclass(frozen=True)
class RoutingDecision:
    """Pre-iteration routing decisions produced by OracleRouter.route().

    All fields are set once per iteration at iteration start. The agent
    reads them; the router produces them. Revision-phase decisions
    (revision_budget, atom_context) are computed post-verification via
    standalone router methods, not included here.
    """
    # Generation
    n_candidates: int
    is_reset: bool
    reset_context: str | None
    disproof_escalation: bool  # True when disproof overlay was appended to reset

    # Verification
    verifier_extra_system: str | None
    next_oracle: OracleType  # v3.8 scaffolding: populated via _ORACLE_ROUTING but not yet consumed by agent
    force_adversarial: bool  # v3.8 scaffolding: will override adversarial addendum in tree-search mode


# --- OracleRouter ---

class OracleRouter:
    """Pure-function routing layer for the GVR loop.

    Reads RunState + EvidenceState, produces RoutingDecision.
    Never mutates state — that remains the agent's responsibility.
    """

    def __init__(
        self,
        config: AgentConfig,
        domain: str,
        adversarial_addendum_fn: Callable[[], str | None],
        reset_addendum_fn: Callable[[], str],
        disproof_addendum_fn: Callable[[], str] | None = None,
    ):
        self._config = config
        self._domain = domain
        self._adversarial_addendum_fn = adversarial_addendum_fn
        self._reset_addendum_fn = reset_addendum_fn
        self._disproof_addendum_fn = disproof_addendum_fn
        self._confidence_floor = config.confidence_threshold * 0.85

    # --- Pre-iteration bundle ---

    def route(self, state, evidence: EvidenceState | None = None) -> RoutingDecision:
        """Produce pre-iteration routing decisions.

        Args:
            state: Current RunState (from prior iterations).
            evidence: EvidenceState from the previous iteration, or None
                      if this is the first iteration.
        """
        is_reset = self.check_stall(state)

        if evidence is not None:
            # NOTE: evidence.error_category is already classified (e.g., "algebra").
            # We look up the routing table directly, NOT call classify_errors_routed()
            # which expects raw critique text. See error_taxonomy.py:_ORACLE_ROUTING.
            next_oracle, force_adversarial = _ORACLE_ROUTING.get(
                evidence.error_category, (OracleType.LAYER3_LLM, False)
            )
        else:
            next_oracle = OracleType.LAYER3_LLM
            force_adversarial = False

        disproof = is_reset and self._should_disproof(state, evidence)

        return RoutingDecision(
            n_candidates=self._compute_n(evidence, is_reset),
            is_reset=is_reset,
            reset_context=(
                self.build_reset_context(state, evidence) if is_reset else None
            ),
            disproof_escalation=disproof,
            verifier_extra_system=self.build_verifier_extra_system(state),
            next_oracle=next_oracle,
            force_adversarial=force_adversarial,
        )

    # --- Individual routing methods (moved from agent.py) ---
    # Each preserves exact logic from the original. See agent.py git history.

    def rank_candidates(self, verifications: list[VerificationResult]) -> int:
        """Select best candidate index. Verdict-aware: better verdicts win,
        confidence breaks ties within the same verdict tier."""
        verdict_rank = {v: i for i, v in enumerate(Verdict)}
        return min(
            range(len(verifications)),
            key=lambda i: (
                verdict_rank[verifications[i].verdict],
                -verifications[i].confidence,
            ),
        )

    def check_stall(self, state) -> bool:
        """Check whether a stall-triggered reset should fire this iteration.

        Moved from MathAgent._check_stall(). Reads state but never mutates it.
        """
        if not self._config.stall_reset:
            return False
        if state.reset_cooldown_remaining > 0:
            return False
        max_resets = max(1, self._config.max_iterations // 4)
        if state.resets_used >= max_resets:
            return False

        if state.iterations_since_meaningful_improvement >= self._config.stall_window:
            return True

        verdicts = state.iteration_final_verdicts
        return (
            len(verdicts) >= 2
            and verdicts[-1] == Verdict.MAJOR_FLAW
            and verdicts[-2] == Verdict.MAJOR_FLAW
        )

    def build_verifier_extra_system(self, state) -> str | None:
        """Build extra_system for verify calls: adversarial addendum + atom focus.

        Moved from MathAgent._build_verifier_extra_system().
        """
        if state.atom_history:
            stability = classify_atom_stability(
                state.atom_history,
                state.confidence_history,
                self._confidence_floor,
            )
            directive = build_atom_focus_directive(state.atom_history[-1], stability)
        else:
            directive = None
        return combine(self._adversarial_addendum_fn(), directive)

    def build_reset_context(
        self, state, evidence: EvidenceState | None = None
    ) -> str:
        """Build the strategy-reset prompt overlay for a reset iteration.

        When Bayesian-adaptive disproof is warranted (false_premise/interpretation/
        counterexample error signal OR consecutive UNSOLVED verdicts), the
        domain-specific disproof addendum is appended to the standard reset.

        Moved from MathAgent._build_reset_context().
        """
        recent = state.failed_approaches[-5:]
        approaches_text = (
            "\n".join(f"- {a}" for a in recent) if recent else "- (none recorded)"
        )

        atom_stability_context = ""
        if state.atom_history and self._config.variant_b is None:
            stability = classify_atom_stability(
                state.atom_history,
                state.confidence_history,
                self._confidence_floor,
            )
            stable_ids = [aid for aid, s in stability.items() if s.value == "stable"]
            if stable_ids:
                atom_stability_context = (
                    f"\n\n## STABLE ATOMS — do not discard these results\n"
                    f"Atoms {stable_ids} were consistent across recent iterations. "
                    f"Build on them rather than abandoning them."
                )

        base = (
            self._reset_addendum_fn()
            .replace("{failed_approaches}", approaches_text)
            .replace("{atom_stability_context}", atom_stability_context)
        )

        if self._should_disproof(state, evidence):
            return combine(base, self._disproof_addendum_fn()) or base

        return base

    def _should_disproof(
        self, state, evidence: EvidenceState | None
    ) -> bool:
        """Bayesian-adaptive check: is disproof escalation warranted?

        Fires when the accumulated evidence suggests the problem's premise may
        be false. Two independent signals (either triggers):

        1. Error taxonomy: the verifier critique classified as false_premise,
           interpretation, or counterexample — direct evidence of premise doubt.
        2. Consecutive UNSOLVED verdicts: the verifier repeatedly says the
           solution doesn't address the problem, suggesting solvability is low.

        Only fires when a disproof addendum is actually configured.
        """
        if self._disproof_addendum_fn is None:
            return False

        # Signal 1: error taxonomy suggests false premise
        _DISPROOF_CATEGORIES = {"false_premise", "interpretation", "counterexample"}
        if (
            evidence is not None
            and evidence.error_category in _DISPROOF_CATEGORIES
        ):
            return True

        # Signal 2: consecutive UNSOLVED verdicts
        verdicts = state.iteration_final_verdicts
        if len(verdicts) >= 2 and all(
            v == Verdict.UNSOLVED for v in verdicts
        ):
            return True

        return False

    def build_atom_context(
        self,
        atom_history: list[list[AtomAnnotation]],
        confidence_history: list[float],
    ) -> str | None:
        """Build atom stability advisory for the reviser.

        Moved from MathAgent._build_atom_context(). Called during revision
        loop with current state (includes current iteration's atoms).
        """
        if len(atom_history) < 2:
            return None

        all_synthetic = all(
            all(a.synthetic for a in iteration_atoms)
            for iteration_atoms in atom_history
        )
        if all_synthetic:
            return None

        if self._config.variant_b is not None:
            return None

        stability = classify_atom_stability(
            atom_history, confidence_history, self._confidence_floor
        )
        if not stability:
            return None

        grouped: dict[AtomStability, list[int]] = {}
        for aid, s in stability.items():
            grouped.setdefault(s, []).append(aid)

        messages = {
            AtomStability.STABLE: (
                " have been stable across iterations. "
                "Do not discard these atoms — they represent verified correct steps."
            ),
            AtomStability.OSCILLATING: (
                " are oscillating — the same approach is being retried without "
                "success. Avoid repeating the same reasoning patterns for these atoms."
            ),
            AtomStability.FAILING: (
                " show declining confidence. "
                "Consider a categorical change of approach for these steps."
            ),
        }

        parts: list[str] = []
        for category, msg in messages.items():
            ids = sorted(grouped.get(category, []))
            if ids:
                ids_str = ", ".join(f"ATOM[{i}]" for i in ids)
                parts.append(f"{ids_str}{msg}")

        if not parts:
            return None
        return "## Atom stability advisory:\n" + "\n".join(parts)

    def revision_budget(self, evidence: EvidenceState) -> int:
        """Compute adaptive revision budget from current evidence.

        Moved from MathAgent._compute_adaptive_revisions(). Called
        post-verification with current-iteration evidence.
        """
        base = self._config.max_revisions_per_cycle
        if (
            evidence.error_category in {"algebra", "citation"}
            and evidence.best_confidence >= 0.80
        ):
            return 1
        if evidence.best_confidence < 0.70:
            return min(base + 1, 5)
        return base

    # --- Private helpers ---

    def _compute_n(self, evidence: EvidenceState | None, is_reset: bool) -> int:
        """Compute candidate count for the next iteration.

        Combines _compute_dynamic_n logic with stall reset N-boost.
        """
        base_n = self._config.best_of_n

        if is_reset:
            return base_n + self._config.reset_n_boost

        if (
            self._config.adaptive_compute
            and evidence is not None
            and evidence.iteration > 0
        ):
            return self._compute_dynamic_n(evidence)

        return base_n

    def _compute_dynamic_n(self, evidence: EvidenceState) -> int:
        """Dynamic N based on difficulty. Moved from MathAgent._compute_dynamic_n()."""
        _escalate_categories = {
            "logic", "missing_case", "interpretation", "units", "counterexample",
            "false_premise",
        }
        base_n = self._config.best_of_n

        if evidence.error_category in _escalate_categories:
            return base_n
        if evidence.error_category in {"algebra", "citation"}:
            return 1
        if evidence.best_confidence < self._config.confidence_threshold * 0.75:
            return base_n
        return 1


# --- Module-level helpers (moved from agent.py) ---

def combine(a: str | None, b: str | None) -> str | None:
    """Combine two optional prompt sections with a blank-line separator.

    Strips leading/trailing newlines from each part. Falsy values (None, "")
    are treated as absent. Returns None when both are absent.
    """
    a_clean = a.strip("\n") if a else None
    b_clean = b.strip("\n") if b else None
    if a_clean and b_clean:
        return a_clean + "\n\n" + b_clean
    return a_clean or b_clean


def build_atom_focus_directive(
    atoms: list[AtomAnnotation],
    stability: dict[int, AtomStability],
) -> str | None:
    """Build a two-tier verifier focus directive from atom stability history.

    Moved from agent.py module-level _build_atom_focus_directive().
    """
    high: list[int] = []
    reduced: list[int] = []

    for a in atoms:
        if a.synthetic:
            continue

        atom_stability = stability.get(a.id, AtomStability.FAILING)
        if atom_stability == AtomStability.STABLE:
            continue

        if a.oracle in _HIGH_ORACLE_TYPES:
            high.append(a.id)
        else:
            layer = _LAYER_BY_ORACLE.get(a.oracle, 3)
            sentinel_texts = parse_layer_results(a.content).get(layer, [])
            has_failure = any(
                marker in text
                for text in sentinel_texts
                for marker in _SENTINEL_FAILURE_MARKERS
            )
            if sentinel_texts and not has_failure:
                reduced.append(a.id)
            else:
                high.append(a.id)

    if not high and not reduced:
        return None

    lines = ["ATOM FOCUS DIRECTIVE:"]
    if high:
        ids = ", ".join(f"ATOM[{i}]" for i in sorted(high))
        lines.append(f"HIGH attention (require explicit justification): {ids}")
    if reduced:
        ids = ", ".join(f"ATOM[{i}]" for i in sorted(reduced))
        lines.append(f"REDUCED attention (skip exhaustive re-derivation): {ids}")
    return "\n".join(lines)
