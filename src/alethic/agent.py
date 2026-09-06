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

from alethic.atoms import (
    AtomAnnotation,
    content_hash,
    parse_atoms,
)
from alethic.breaker import BreakerResult, run_breaker
from alethic.client_factory import get_client
from alethic.error_taxonomy import classify_errors, classify_inconsistency, get_revision_addendum
from alethic.exceptions import CheckpointError, ContextExhaustedError, TruncatedResponseError
from alethic.llm import API_ERRORS, ModelClient
from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    BreakerVerdict,
    EventType,
    EvidenceState,
    SearchConfig,
    Solution,
    TokenLedger,
    Verdict,
    VerificationResult,
)
from alethic.oracle_router import OracleRouter
from alethic.prompts import (
    ADVERSARIAL_VERIFIER_ADDENDUM,
    DISPROOF_STRATEGY_ADDENDUM,
    GENERATOR_SYSTEM,
    SATURATION_AWARENESS_ADDENDUM,
    STRATEGY_RESET_ADDENDUM,
    SURVEY_GENERATOR_GUIDANCE,
    SURVEY_VERIFIER_GUIDANCE,
    TOOL_GUIDANCE,
    VERIFIER_SYSTEM,
)
from alethic.session import (
    create_session_dir,
    load_checkpoint,
    write_checkpoint,
    write_tree_checkpoint,
)
from alethic.subagents import _strip_sentinels, generate, revise, verify
from alethic.surveyor import SurveyResult, format_survey_block, survey

logger = logging.getLogger("alethic")

_EMPTY_REASONS = frozenset({"n/a", "na", "none", "not applicable", ""})


def rank_candidates(verifications: list[VerificationResult]) -> int:
    """Public API — delegates to OracleRouter with default config."""
    from alethic.oracle_router import OracleRouter
    router = OracleRouter(AgentConfig(), domain="math",
                          adversarial_addendum_fn=lambda: None,
                          reset_addendum_fn=lambda: "")
    return router.rank_candidates(verifications)


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
    iteration_final_verdicts: deque = field(default_factory=lambda: deque(maxlen=2))
    resets_used: int = 0
    reset_cooldown_remaining: int = 0
    # Atom tracking (for atom-aware stall recovery)
    atom_history: list[list[AtomAnnotation]] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    breaker_falsified: bool = False
    # Saturation tracking: (iteration_index, error_category) per iteration.
    # Only category labels (e.g. "algebra", "interpretation") are recorded —
    # never critique text — so this can safely cross into the verifier without
    # breaking decoupling.
    critique_category_history: list[tuple[int, str]] = field(default_factory=list)

    @property
    def best_solution_text(self) -> str | None:
        """Return the best solution text, or None if no solution has been recorded."""
        return self.best_solution.solution_text if self.best_solution else None

    def stall_state_dict(self) -> dict:
        """Serialize stall detection state for checkpoint persistence."""
        return {
            "iterations_since_meaningful_improvement": (
                self.iterations_since_meaningful_improvement
            ),
            "iteration_final_verdicts": [
                v.value if isinstance(v, Verdict) else str(v) for v in self.iteration_final_verdicts
            ],
            "resets_used": self.resets_used,
            "reset_cooldown_remaining": self.reset_cooldown_remaining,
            "atom_history": [
                {
                    "id": a.id,
                    "content_hash": content_hash(a),
                    "iteration": i,
                    "confidence": conf,
                }
                for i, (atoms, conf) in enumerate(
                    zip(self.atom_history, self.confidence_history, strict=True)
                )
                for a in atoms
                if not a.synthetic
            ],
        }


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

    if len(summary) <= 200:
        return summary
    # Truncate at last space before 200 chars, add ellipsis
    cut = summary[:197].rfind(" ")
    return summary[: cut if cut > 0 else 197] + "..."


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
        *,
        client: ModelClient | None = None,
    ):
        self.config = config or AgentConfig()
        self._api_key = api_key
        self._client_injected = client is not None
        self.client = client if client is not None else get_client(api_key=api_key, config=self.config)
        self.router = OracleRouter(
            config=self.config,
            domain=self._domain(),
            adversarial_addendum_fn=self._adversarial_addendum,
            reset_addendum_fn=self._reset_addendum,
            disproof_addendum_fn=self._disproof_addendum,
            saturation_addendum_fn=self._saturation_addendum,
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

    def _domain(self) -> str:
        """Return the domain string for session metadata."""
        return "math"

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

    @property
    def _confidence_floor(self) -> float:
        return self.config.confidence_threshold * 0.85

    def _build_system_prompt(self, role: str, base: str) -> str:
        """Append tool guidance overlays to a base system prompt."""
        guide_map = self._get_tool_guidance_map()
        parts = [base]
        for tool in sorted(self.config.tool_guidance):
            if tool in guide_map and role in guide_map[tool]:
                parts.append(guide_map[tool][role])
        return "".join(parts)

    def _reset_addendum(self) -> str:
        """Return the strategy reset prompt template for this domain.

        Override in subclasses to use domain-specific reset prompts.
        """
        return STRATEGY_RESET_ADDENDUM

    def _disproof_addendum(self) -> str:
        """Return the disproof escalation overlay for this domain.

        Appended to the reset context when Bayesian-adaptive disproof is
        warranted. Override in subclasses for domain-specific disproof prompts.
        """
        return DISPROOF_STRATEGY_ADDENDUM

    def _saturation_addendum(self) -> str:
        """Return the saturation-awareness overlay for this domain.

        Appended to verifier extra_system when a critique category has fired
        repeatedly. Override in subclasses for domain-specific phrasing.
        """
        return SATURATION_AWARENESS_ADDENDUM

    def _survey_guidance(self, role: str) -> str:
        """Return role-specific guidance suffix for the surveyor scaffolding.

        `role` is "generator" or "verifier". Override in subclasses for
        domain-specific phrasing.
        """
        if role == "generator":
            return SURVEY_GENERATOR_GUIDANCE
        if role == "verifier":
            return SURVEY_VERIFIER_GUIDANCE
        return ""

    def _breaker_domain(self) -> str:
        """Return the domain string for the adversarial breaker. Override in subclasses."""
        return "math"

    def _run_breaker(
        self,
        problem: str,
        solution_text: str,
        atoms: list,
        ledger: TokenLedger | None,
    ) -> BreakerResult:
        """Run the adversarial breaker against a verified-correct solution.

        Returns NO_FLAW_FOUND immediately if breaker is disabled or
        solution has only synthetic atoms (monolithic fallback).
        """
        if not self.config.adversarial_breaker:
            return BreakerResult(
                verdict=BreakerVerdict.NO_FLAW_FOUND,
                target_atom=0, flaw_type="none",
                evidence="Breaker disabled.", reasoning="",
            )
        # Skip for monolithic solutions (all atoms synthetic, or no atoms)
        real_atoms = [a for a in atoms if not a.synthetic]
        if not real_atoms:
            return BreakerResult(
                verdict=BreakerVerdict.NO_FLAW_FOUND,
                target_atom=0, flaw_type="none",
                evidence="No atom annotations — breaker skipped.", reasoning="",
            )
        return run_breaker(
            self.client,
            problem=problem,
            solution_text=solution_text,
            atoms=real_atoms,
            config=self.config,
            domain=self._breaker_domain(),
            ledger=ledger,
        )

    def _adversarial_addendum(self) -> str | None:
        """Return the adversarial verifier addendum if enabled, else None.

        Override in subclasses to use domain-specific adversarial prompts.
        """
        if not self.config.adversarial_self_correction:
            return None
        return ADVERSARIAL_VERIFIER_ADDENDUM


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
        token_ledger: TokenLedger | None = None,
        session_dir: str | None = None,
        checkpoint_path: str | None = None,
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
            token_ledger=token_ledger,
            session_dir=session_dir,
            checkpoint_path=checkpoint_path,
        )

    def _check_false_premise(
        self,
        verification: VerificationResult,
        problem: str,
        iteration: int,
        state: RunState,
        log: EventLog,
        token_ledger: TokenLedger | None = None,
        session_dir: str | None = None,
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
                token_ledger=token_ledger,
                session_dir=session_dir,
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
        ledger: TokenLedger | None = None,
        context_limit: int = 200_000,
        context_threshold: float = 0.8,
    ) -> list[tuple[Solution, float]]:
        """Generate N candidates. Parallel (ThreadPoolExecutor) when N>1, sequential when N=1."""

        def _gen_one(client: ModelClient, config: AgentConfig) -> tuple[Solution, float]:
            t0 = time.time()
            sol = generate(
                client,
                problem=problem,
                config=config,
                iteration=iteration,
                balanced=balanced,
                failed_approaches=failed_approaches,
                reset_context=reset_context,
                system_prompt=prompts.get("generator_system"),
                user_template=prompts.get("generator_user"),
                balanced_addendum=prompts.get("balanced_addendum"),
                ledger=ledger,
                context_limit=context_limit,
                context_threshold=context_threshold,
            )
            return sol, time.time() - t0

        if n == 1:
            return [_gen_one(self.client, self.config)]

        # Build variant B config and client when variant_b is set
        variant_b_config: AgentConfig | None = None
        variant_b_client: ModelClient | None = None
        if self.config.variant_b is not None:
            variant_b_config = self.config.build_variant_b_config()
            same_endpoint = (
                variant_b_config.provider == self.config.provider
                and variant_b_config.base_url == self.config.base_url
            )
            same_options = (
                variant_b_config.request_options == self.config.request_options
                and variant_b_config.token_parameter == self.config.token_parameter
            )
            if not (same_endpoint and same_options and (
                self._client_injected or variant_b_config.model == self.config.model
            )):
                variant_b_client = get_client(
                    api_key=self._api_key if same_endpoint else None, config=variant_b_config,
                )
            else:
                variant_b_client = self.client

        results: list[tuple[Solution, float]] = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {}
            for i in range(n):
                # Even indices (0,2,4...) → variant A; odd indices (1,3,5...) → variant B
                if variant_b_config is not None and variant_b_client is not None and i % 2 == 1:
                    fut = pool.submit(_gen_one, variant_b_client, variant_b_config)
                else:
                    fut = pool.submit(_gen_one, self.client, self.config)
                futures[fut] = i + 1  # 1-based index for logging
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
        state: RunState,
        ledger: TokenLedger | None = None,
        context_limit: int = 200_000,
        context_threshold: float = 0.8,
    ) -> list[tuple[Solution, VerificationResult, float, float, int]]:
        """Verify all candidates sequentially. Return list sorted by confidence desc.

        Returns list of (solution, verification, gen_time, verify_time, orig_idx)
        tuples where orig_idx is the 1-based generation-order index.
        """
        verified: list[tuple[Solution, VerificationResult, float, float, int]] = []
        _extra_system = self.router.build_verifier_extra_system(state)

        for idx, (solution, gen_time) in enumerate(candidates, 1):
            # Skip empty candidates — no point burning verifier tokens
            if not solution.solution_text or solution.solution_text == "[No response generated]":
                verified.append((
                    solution,
                    VerificationResult(
                        verdict=Verdict.UNSOLVED,
                        confidence=1.0,
                        critique="No solution was generated (empty model response).",
                        reason="Empty response — model produced no text output.",
                    ),
                    gen_time,
                    0.0,
                    idx,
                ))
                continue
            t0 = time.time()
            verification = verify(
                self.client,
                problem=problem,
                solution=solution,
                config=self.config,
                system_prompt=prompts.get("verifier_system"),
                user_template=prompts.get("verifier_user"),
                extra_system=_extra_system,
                ledger=ledger,
                context_limit=context_limit,
                context_threshold=context_threshold,
            )
            verify_time = time.time() - t0
            verified.append((solution, verification, gen_time, verify_time, idx))

        # Select best candidate via router.rank_candidates, then sort rest by confidence
        best_idx = self.router.rank_candidates([v for _, v, _, _, _ in verified])
        best = verified.pop(best_idx)
        verified.sort(key=lambda x: x[1].confidence, reverse=True)
        verified.insert(0, best)
        return verified

    def _log_candidates(
        self,
        verified: list[tuple[Solution, VerificationResult, float, float, int]],
        generation_wall_time: float,
    ) -> None:
        """Log generation wall time and a ranked table of candidates (N>1 only)."""
        self._log(
            f"[BEST-OF-N] Generated {len(verified)} candidates "
            f"(wall time: {generation_wall_time:.1f}s)"
        )
        self._log(f"{'Rank':<6}{'Verdict':<16}{'Confidence':<13}{'Gen(s)':<10}{'Ver(s)':<10}")
        self._log(f"{'─' * 55}")
        for rank, (_sol, ver, gen_t, ver_t, _orig_idx) in enumerate(verified, 1):
            self._log(
                f"{rank:<6}{ver.verdict.value:<16}{ver.confidence:<13.0%}"
                f"{gen_t:<10.1f}{ver_t:<10.1f}"
            )

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
        ledger: TokenLedger | None = None,
        context_limit: int = 200_000,
        context_threshold: float = 0.8,
        session_dir: str | None = None,
    ) -> AgentResult | None:
        """Run revision sub-loop. Returns AgentResult if solved, else None (mutates state)."""
        effective_max_revisions = (
            max_revisions if max_revisions is not None else self.config.max_revisions_per_cycle
        )
        current_solution = solution

        for rev_num in range(1, effective_max_revisions + 1):
            self._log(f"[REVISE] Revision {rev_num}/{effective_max_revisions}...")
            revision_addendum = get_revision_addendum(classify_errors(verification.critique))
            current_solution = revise(
                self.client,
                problem=problem,
                solution=current_solution,
                verification=verification,
                config=self.config,
                revision_number=rev_num,
                system_prompt=prompts.get("reviser_system"),
                user_template=prompts.get("reviser_user"),
                critique_addendum=revision_addendum,
                atom_context=self.router.build_atom_context(state.atom_history, state.confidence_history),
                ledger=ledger,
                context_limit=context_limit,
                context_threshold=context_threshold,
            )
            state.total_revisions += 1
            log.emit(
                EventType.REVISE,
                iteration,
                revision=rev_num,
            )

            # Patch #2 (PR #9) observability: emit event when reviser declined/dismissed
            # every issue. Solution is effectively unchanged; the orchestrator continues
            # without special handling (respects decoupled-verification invariant).
            triage = current_solution.triage_summary or {}
            if triage and triage.get("accept", 0) == 0 and sum(triage.values()) > 0:
                log.emit(
                    EventType.REVISER_ALL_DECLINED,
                    iteration,
                    revision=rev_num,
                    triage_summary=dict(triage),
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
                extra_system=self.router.build_verifier_extra_system(state),
                ledger=ledger,
                context_limit=context_limit,
                context_threshold=context_threshold,
            )
            log.emit(
                EventType.VERIFY,
                iteration,
                revision=rev_num,
                verdict=verification.verdict.value,
                confidence=verification.confidence,
            )
            self._log(
                f"[VERIFY] Verdict: {verification.verdict.value} "
                f"(confidence: {verification.confidence:.0%})"
            )

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
                    token_ledger=ledger,
                    session_dir=session_dir,
                )

            # If major flaw after revision, break to restart from generator
            if verification.verdict == Verdict.MAJOR_FLAW:
                self._log("[REVISE] Major flaw persists — restarting from generator")
                break

            # If unsolved after revision, check false premise then restart
            if verification.verdict == Verdict.UNSOLVED:
                fp = self._check_false_premise(
                    verification,
                    problem,
                    iteration,
                    state,
                    log,
                    token_ledger=ledger,
                    session_dir=session_dir,
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
        resume_from: str | None = None,
        create_session: bool = True,
    ) -> AgentResult:
        """Solve a mathematical problem using the Generate → Verify → Revise loop.

        Args:
            problem: The mathematical problem statement.
            balanced: Use balanced prompting (explore counterexamples first).
            resume_from: Path to a session directory to resume from (checkpoint).
            create_session: Create a session directory for persistence (default True).
                Set to False for batch experiments to avoid filesystem contention.

        Returns:
            AgentResult with the solution (or admitted failure).
        """
        if self.config.search_mode == "tree":
            return self._solve_tree(
                problem,
                balanced=balanced,
                resume_from=resume_from,
                create_session=create_session,
            )

        state = RunState()
        log = EventLog()
        raw_threshold = self.config.confidence_threshold
        # Confidence calibration: load temperature-scaled threshold
        calibrated_threshold = raw_threshold  # default: identity (no calibration)
        if self.config.apply_calibration:
            try:
                from pathlib import Path as _Path

                from alethic.calibration import load_calibrated_threshold

                _store_path = (
                    _Path(self.config.calibration_store)
                    if self.config.calibration_store
                    else None
                )
                calibrated_threshold = load_calibrated_threshold(
                    raw_threshold, store_path=_store_path
                )
            except Exception:
                pass  # calibration failure is non-fatal; fall back to raw threshold
        threshold = calibrated_threshold
        prompts = self._prompt_set()

        # Build generator system prompt with tool guidance
        gen_base = prompts.get("generator_system") or GENERATOR_SYSTEM
        prompts["generator_system"] = self._build_system_prompt("generator", gen_base)

        # Build verifier system prompt with tool guidance
        ver_base = prompts.get("verifier_system") or VERIFIER_SYSTEM
        prompts["verifier_system"] = self._build_system_prompt("verifier", ver_base)

        # Pre-flight surveyor: one-shot pass that sees ONLY the problem statement.
        # Output is appended to both generator and verifier system prompts.
        # Decoupling is preserved because the surveyor never sees any solution.
        survey_result: SurveyResult = SurveyResult()
        if self.config.enable_surveyor:
            self._log("[SURVEY] Running pre-flight surveyor")
            survey_result = survey(problem, self.client, self.config)
            if not survey_result.is_empty:
                block = format_survey_block(survey_result, role="generator")
                prompts["generator_system"] = (
                    prompts["generator_system"] + block + self._survey_guidance("generator")
                )
                prompts["verifier_system"] = (
                    prompts["verifier_system"]
                    + format_survey_block(survey_result, role="verifier")
                    + self._survey_guidance("verifier")
                )
                self._log(
                    f"[SURVEY] {len(survey_result.pitfalls)} pitfalls, "
                    f"{len(survey_result.methods)} methods, "
                    f"{len(survey_result.sanity_checks)} sanity-check candidates"
                )
            else:
                self._log("[SURVEY] Empty result — no scaffolding injected")

        # Initialize token ledger and context tracking
        ledger = TokenLedger()
        context_limit = self.config.resolved_context_window

        # Resume from checkpoint if requested
        start_iteration = 1
        session_dir: str | None = None
        if resume_from:
            if os.path.exists(os.path.join(resume_from, "tree_state.json")):
                raise CheckpointError(
                    f"Session at {resume_from} is a tree-mode checkpoint "
                    "(tree_state.json present). Resume it with search_mode='tree' "
                    "(CLI: --search tree)."
                )
            checkpoint = load_checkpoint(resume_from)
            saved_problem = checkpoint.get("problem", "")
            if saved_problem and problem != saved_problem:
                logger.warning(
                    "Resume problem mismatch: checkpoint was for a different problem. "
                    "Checkpoint: %s...",
                    saved_problem[:80],
                )
            session_dir = resume_from
            start_iteration = checkpoint["current_iteration"] + 1
            state.best_confidence = checkpoint["best_confidence"]
            state.failed_approaches = checkpoint.get("failed_approaches", [])
            # Restore stall state
            ss = checkpoint.get("stall_state", {})
            state.iterations_since_meaningful_improvement = ss.get(
                "iterations_since_meaningful_improvement", 0
            )
            valid_verdicts = {e.value for e in Verdict}
            for v in ss.get("iteration_final_verdicts", []):
                if isinstance(v, str) and v in valid_verdicts:
                    state.iteration_final_verdicts.append(Verdict(v))
            state.resets_used = ss.get("resets_used", 0)
            state.reset_cooldown_remaining = ss.get("reset_cooldown_remaining", 0)
            # Restore best solution
            best_text = checkpoint.get("best_solution_text")
            if best_text:
                state.best_solution = Solution(
                    problem=problem, solution_text=best_text, iteration=0
                )
            # Restore ledger
            ledger = TokenLedger.from_dict(checkpoint.get("token_ledger", {}))
            self._log(
                f"[RESUME] Resuming from iteration {start_iteration} "
                f"(conf: {state.best_confidence:.0%})"
            )
        elif create_session:
            try:
                session_dir = create_session_dir(
                    problem=problem,
                    domain=self._domain(),
                    config=self.config,
                )
            except OSError:
                logger.warning("Failed to create session directory")
                session_dir = None

        self._log(f"{'=' * 60}")
        self._log(self._log_header())
        self._log(f"Model: {self.config.model}")
        self._log(f"Max iterations: {self.config.max_iterations}")
        self._log(f"Confidence threshold: {threshold:.0%}")
        self._log(
            f"Code execution: {'enabled' if self.config.enable_code_execution else 'disabled'}"
        )
        if self.config.best_of_n > 1:
            self._log(f"Best-of-N: {self.config.best_of_n} candidates per iteration (parallel)")
        if self.config.stall_reset:
            self._log(
                f"Stall reset: enabled (window={self.config.stall_window}, "
                f"epsilon={self.config.stall_epsilon})"
            )
        self._log(f"{'=' * 60}")
        self._log(f"Problem: {problem[:200]}{'...' if len(problem) > 200 else ''}")
        self._log("")

        evidence_state: EvidenceState | None = None
        evidence_conf_history: list[float] = []

        for iteration in range(start_iteration, self.config.max_iterations + 1):
            self._log(f"{'─' * 40}")
            self._log(f"Iteration {iteration}/{self.config.max_iterations}")
            self._log(f"{'─' * 40}")

            try:
                pre_iter_best = state.best_confidence

                # ── ROUTING DECISION (pre-iteration) ──
                decision = self.router.route(state, evidence_state)
                is_reset = decision.is_reset
                n_this_iter = decision.n_candidates
                reset_context = decision.reset_context

                if is_reset:
                    reason = (
                        "major_flaw_streak"
                        if (
                            len(state.iteration_final_verdicts) >= 2
                            and state.iteration_final_verdicts[-1] == Verdict.MAJOR_FLAW
                            and state.iteration_final_verdicts[-2] == Verdict.MAJOR_FLAW
                        )
                        else "no_progress"
                    )
                    state.resets_used += 1
                    state.reset_cooldown_remaining = 1
                    # Clear atom history on reset — new strategy, fresh tracking
                    state.atom_history.clear()
                    state.confidence_history.clear()
                    disproof_tag = " + DISPROOF" if decision.disproof_escalation else ""
                    self._log(
                        f"[STALL RESET{disproof_tag}] Triggered (reason: {reason}) — "
                        f"N={n_this_iter}, max_revisions=1"
                    )
                    log.emit(
                        EventType.STALL_RESET,
                        iteration,
                        reason=reason,
                        n_override=n_this_iter,
                        max_revisions_override=1,
                        resets_used=state.resets_used,
                        stall_counter=state.iterations_since_meaningful_improvement,
                        disproof_escalation=decision.disproof_escalation,
                    )
                else:
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
                    ledger=ledger,
                    context_limit=context_limit,
                    context_threshold=self.config.context_threshold,
                )
                gen_wall_time = time.time() - gen_t0

                if not candidates:
                    self._log("[GENERATE] All candidates failed — skipping iteration")
                    log.emit(EventType.ERROR, iteration, error="all candidates failed")
                    continue

                n_failed = n_this_iter - len(candidates)
                if n_failed > 0:
                    log.emit(
                        EventType.ERROR,
                        iteration,
                        error=f"{n_failed}/{n_this_iter} candidates failed",
                    )
                    logger.info(
                        "Generated %d/%d candidates (%d failed)",
                        len(candidates),
                        n_this_iter,
                        n_failed,
                    )
                    self._log(
                        f"[GENERATE] Warning: {len(candidates)}/{n_this_iter} candidates "
                        f"succeeded ({n_failed} failed)"
                    )

                for idx, (sol, gen_t) in enumerate(candidates, 1):
                    log.emit(
                        EventType.GENERATE,
                        iteration,
                        candidate=idx,
                        solution_preview=sol.solution_text[:500],
                    )
                    self._log(
                        f"[GENERATE] Candidate {idx}/{len(candidates)} produced "
                        f"({len(sol.solution_text)} chars, {gen_t:.1f}s)"
                    )

                # ── VERIFY (decoupled) ──
                self._log("[VERIFY] Independently verifying...")
                verified = self._verify_candidates(
                    problem=problem,
                    candidates=candidates,
                    prompts=prompts,
                    state=state,
                    ledger=ledger,
                    context_limit=context_limit,
                    context_threshold=self.config.context_threshold,
                )

                # Log rankings for N>1
                if n_this_iter > 1:
                    self._log_candidates(verified, gen_wall_time)

                # Record all in log (use original generation-order index)
                for _sol, ver, _gen_t, _ver_t, orig_idx in verified:
                    _inc_result = classify_inconsistency(ver.critique)
                    log.emit(
                        EventType.VERIFY,
                        iteration,
                        candidate=orig_idx,
                        verdict=ver.verdict.value,
                        confidence=ver.confidence,
                        num_issues=len(ver.issues),
                        error_category=_inc_result.primary,
                        error_level=_inc_result.level,
                    )

                # Best candidate is first (sorted by confidence desc)
                solution, verification, _, _, _ = verified[0]
                iteration_verdict = verification.verdict

                # Parse atom annotations from winning solution
                state.breaker_falsified = False
                atoms = parse_atoms(solution.solution_text)
                if atoms:
                    max_hist = self.config.stall_window + 1
                    state.atom_history.append(atoms)
                    state.confidence_history.append(verification.confidence)
                    if len(state.atom_history) > max_hist:
                        state.atom_history = state.atom_history[-max_hist:]
                        state.confidence_history = state.confidence_history[-max_hist:]

                # Update EvidenceState for adaptive compute (next iter) and revision budget
                error_cat = classify_errors(verification.critique)
                state.critique_category_history.append((iteration, error_cat))
                evidence_conf_history.append(state.best_confidence)
                evidence_state = EvidenceState(
                    iteration=iteration,
                    best_confidence=state.best_confidence,
                    error_category=error_cat,
                    confidence_history=evidence_conf_history,
                )

                self._log(
                    f"[VERIFY] Best: {verification.verdict.value} "
                    f"(confidence: {verification.confidence:.0%})"
                )
                if verification.issues:
                    for issue in verification.issues:
                        self._log(f"  Issue: {str(issue)[:100]}")

                # Track best solution seen
                if verification.confidence > state.best_confidence:
                    state.best_confidence = verification.confidence
                    state.best_solution = solution

                # ── CHECK: Is it correct? ──
                if verification.is_acceptable(threshold):
                    # ── BREAKER: adversarial probe before accepting ──
                    atoms_for_breaker = state.atom_history[-1] if state.atom_history else []
                    breaker_result = self._run_breaker(
                        problem=problem,
                        solution_text=solution.solution_text,
                        atoms=atoms_for_breaker,
                        ledger=ledger,
                    )
                    if breaker_result.verdict == BreakerVerdict.FLAW_FOUND:
                        self._log(
                            f"[BREAKER] Flaw found in atom {breaker_result.target_atom} "
                            f"({breaker_result.flaw_type}) — demoting to MAJOR_FLAW"
                        )
                        log.emit(
                            EventType.BREAKER_FLAW_FOUND,
                            iteration,
                            target_atom=breaker_result.target_atom,
                            flaw_type=breaker_result.flaw_type,
                            evidence=breaker_result.evidence,
                        )
                        state.breaker_falsified = True
                        # Inject breaker critique into verification for revision
                        verification = VerificationResult(
                            verdict=Verdict.MAJOR_FLAW,
                            critique=(
                                f"{verification.critique}\n\n"
                                f"{breaker_result.critique_addendum}"
                            ),
                            confidence=min(verification.confidence, 0.5),
                            issues=verification.issues,
                            reason=verification.reason,
                            section_confidences=verification.section_confidences,
                            corrected_solution=None,
                        )
                        iteration_verdict = Verdict.MAJOR_FLAW
                    elif breaker_result.verdict == BreakerVerdict.SUSPECTED_FLAW:
                        self._log(
                            f"[BREAKER] Suspected flaw in atom {breaker_result.target_atom} "
                            f"— continuing with caution"
                        )
                        log.emit(
                            EventType.BREAKER_SUSPECTED,
                            iteration,
                            target_atom=breaker_result.target_atom,
                            flaw_type=breaker_result.flaw_type,
                        )
                        # For SUSPECTED_FLAW: still accept (threshold already passed),
                        # but log the concern.
                        log.emit(
                            EventType.BREAKER_SURVIVED,
                            iteration,
                            verdict="suspected_flaw_accepted",
                            confidence=verification.confidence,
                        )
                    else:
                        self._log("[BREAKER] No flaw found — solution accepted")
                        log.emit(
                            EventType.BREAKER_SURVIVED,
                            iteration,
                            verdict="no_flaw_found",
                            confidence=verification.confidence,
                        )

                    # If breaker found a definite flaw, skip acceptance
                    if breaker_result.verdict != BreakerVerdict.FLAW_FOUND:
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
                            token_ledger=ledger,
                            session_dir=session_dir,
                        )

                # ── CHECK: False premise? ──
                fp = self._check_false_premise(
                    verification,
                    problem,
                    iteration,
                    state,
                    log,
                    token_ledger=ledger,
                    session_dir=session_dir,
                )
                if fp:
                    return fp

                # ── FIXABLE shortcut: use verifier's correction directly ──
                if verification.has_correction:
                    self._log("[FIXABLE] Verifier provided corrected solution — re-verifying...")
                    corrected = Solution(
                        problem=problem,
                        solution_text=_strip_sentinels(
                            verification.corrected_solution  # type: ignore[arg-type]
                        ),
                        iteration=solution.iteration,
                    )
                    re_verification = verify(
                        self.client,
                        problem=problem,
                        solution=corrected,
                        config=self.config,
                        system_prompt=prompts.get("verifier_system"),
                        user_template=prompts.get("verifier_user"),
                        extra_system=self._adversarial_addendum(),
                        ledger=ledger,
                        context_limit=context_limit,
                        context_threshold=self.config.context_threshold,
                    )
                    log.emit(
                        EventType.VERIFY,
                        iteration,
                        source="fixable_correction",
                        verdict=re_verification.verdict.value,
                        confidence=re_verification.confidence,
                    )
                    self._log(
                        f"[VERIFY] Re-verification: {re_verification.verdict.value} "
                        f"(confidence: {re_verification.confidence:.0%})"
                    )
                    if re_verification.confidence > state.best_confidence:
                        state.best_confidence = re_verification.confidence
                        state.best_solution = corrected
                    if re_verification.is_acceptable(raw_threshold):
                        self._log("")
                        self._log("[SOLVED] Verifier-corrected solution accepted!")
                        log.emit(
                            EventType.ACCEPT,
                            iteration,
                            confidence=re_verification.confidence,
                            verdict=re_verification.verdict.value,
                            source="fixable_correction",
                        )
                        return self._make_result(
                            problem=problem,
                            solution=corrected.solution_text,
                            verdict=Verdict.CORRECT,
                            confidence=re_verification.confidence,
                            iterations_used=iteration,
                            admitted_failure=False,
                            state=state,
                            log=log,
                            token_ledger=ledger,
                            session_dir=session_dir,
                        )
                    # Correction failed re-verification — fall through to normal revision
                    self._log(
                        "[FIXABLE] Correction failed re-verification — falling back to reviser"
                    )
                    verification = re_verification
                    solution = corrected

                # ── REVISE (best candidate) ──
                if verification.needs_revision(threshold):
                    if is_reset:
                        revisions_this_iter: int | None = 1
                    elif self.config.adaptive_revision_budget and evidence_state is not None:
                        revisions_this_iter = self.router.revision_budget(evidence_state)
                    else:
                        revisions_this_iter = None
                    result = self._run_revision_loop(
                        problem=problem,
                        solution=solution,
                        verification=verification,
                        prompts=prompts,
                        iteration=iteration,
                        state=state,
                        log=log,
                        threshold=threshold,
                        max_revisions=revisions_this_iter,
                        ledger=ledger,
                        context_limit=context_limit,
                        context_threshold=self.config.context_threshold,
                        session_dir=session_dir,
                    )
                    if result is not None:
                        return result
                else:
                    self._log(
                        "[GENERATE] Solution unsolvable in this attempt — will retry from scratch"
                    )

                # ── UPDATE STALL TRACKING ──
                state.iteration_final_verdicts.append(iteration_verdict)
                if state.best_confidence > pre_iter_best + self.config.stall_epsilon:
                    state.iterations_since_meaningful_improvement = 0
                else:
                    state.iterations_since_meaningful_improvement += 1

                # ── ACCUMULATE FAILED APPROACH ──
                summary = _summarize_failed_approach(verification)
                state.failed_approaches.append(summary)

                # ── INCREMENTAL STATE PERSISTENCE ──
                if session_dir:
                    try:
                        write_checkpoint(
                            session_dir=session_dir,
                            current_iteration=iteration,
                            best_confidence=state.best_confidence,
                            best_solution_text=state.best_solution_text,
                            failed_approaches=state.failed_approaches,
                            stall_state=state.stall_state_dict(),
                            token_ledger=ledger,
                            status="running",
                        )
                    except (OSError, TypeError) as write_err:
                        logger.warning("Failed to write incremental checkpoint: %s", write_err)

            except ContextExhaustedError:
                self._log(f"\n[CHECKPOINT] Context exhausted at iteration {iteration}")
                checkpoint_path = session_dir
                try:
                    if session_dir:
                        write_checkpoint(
                            session_dir=session_dir,
                            current_iteration=iteration,
                            best_confidence=state.best_confidence,
                            best_solution_text=state.best_solution_text,
                            failed_approaches=state.failed_approaches,
                            stall_state=state.stall_state_dict(),
                            token_ledger=ledger,
                            status="checkpoint",
                        )
                        self._log(f"Session saved to: {session_dir}")
                        self._log(f"Resume with: --resume {session_dir}")
                except Exception as cp_err:
                    logger.warning("Failed to write checkpoint: %s", cp_err)
                    checkpoint_path = None

                return self._make_result(
                    problem=problem,
                    solution=state.best_solution_text,
                    verdict=Verdict.UNSOLVED,
                    confidence=state.best_confidence,
                    iterations_used=iteration,
                    admitted_failure=False,
                    state=state,
                    log=log,
                    token_ledger=ledger,
                    session_dir=session_dir,
                    checkpoint_path=checkpoint_path,
                )

            except TruncatedResponseError as e:
                logger.warning("Iteration %d: response truncated: %s", iteration, e)
                log.emit(EventType.ERROR, iteration, error=f"truncated: {e}")
                continue

            except API_ERRORS as e:
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

        result = self._make_result(
            problem=problem,
            solution=state.best_solution_text,
            verdict=Verdict.UNSOLVED,
            confidence=state.best_confidence,
            iterations_used=self.config.max_iterations,
            admitted_failure=True,
            state=state,
            log=log,
            token_ledger=ledger,
            session_dir=session_dir,
        )

        # Write autopsy for failed loops (1.4)
        if result.session_dir:
            try:
                from alethic.autopsy import generate_autopsy

                autopsy = generate_autopsy(result, client=self.client, config=self.config)
                autopsy_path = os.path.join(result.session_dir, "worklog", "autopsy.md")
                os.makedirs(os.path.dirname(autopsy_path), exist_ok=True)
                with open(autopsy_path, "w", encoding="utf-8") as f:
                    f.write(autopsy)
                logger.info("[AUTOPSY] Written to %s", autopsy_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[AUTOPSY] Failed to generate autopsy: %s", exc)

        # Record calibration datapoint for future temperature fitting
        if self.config.apply_calibration and state.best_confidence > 0.0:
            try:
                from pathlib import Path as _Path

                from alethic.calibration import append_pair

                _store_path = (
                    _Path(self.config.calibration_store)
                    if self.config.calibration_store
                    else None
                )
                preset_name = getattr(self.config, "_preset_name", None) or "unknown"
                append_pair(
                    state.best_confidence,
                    result.verdict == Verdict.CORRECT,
                    model=self.config.model,
                    preset=preset_name,
                    best_of_n=self.config.best_of_n,
                    store_path=_store_path,
                )
            except Exception:
                pass  # calibration write failure is non-fatal

        return result

    def _solve_tree(
        self,
        problem: str,
        *,
        balanced: bool,
        resume_from: str | None,
        create_session: bool,
    ) -> AgentResult:
        """v3.8 tree-search dispatch: delegate to ``search.solve()``.

        Mirrors the flat path's session handling: a session directory is
        created (or reused on resume), ``search.solve`` self-checkpoints on
        context exhaustion, and the final status is recorded via
        ``write_tree_checkpoint``.

        Design notes:

        * **Exhaustion divergence**: when *session_dir* is ``None`` (e.g.
          ``create_session=False``), context-exhaustion errors
          (``ContextExhaustedError``, ``TruncatedResponseError``) propagate
          directly to the caller.  The flat path catches them and returns a
          partial UNSOLVED result; the tree path does not.  This is deliberate:
          library callers that opt out of session creation receive the raw
          exception so they can handle it themselves.

        * **Token-ledger restart on resume**: a fresh ``TokenLedger`` is
          created for every call.  The checkpoint's accumulated ledger is not
          merged, so cost accounting restarts per process rather than
          accumulating across resumptions.

        * **Flat-only features**: confidence calibration (``apply_calibration``)
          and the autopsy-on-UNSOLVED report are not run on the tree path.
          Both require flat-path ``RunState`` internals that have no equivalent
          in the tree search.
        """
        from alethic import search as proof_search

        start_time = time.time()
        ledger = TokenLedger()

        session_dir: str | None = None
        if resume_from is not None:
            if not os.path.exists(os.path.join(resume_from, "tree_state.json")):
                raise CheckpointError(
                    f"Session at {resume_from} has no tree_state.json — it is a "
                    "flat-mode checkpoint. Resume it with search_mode='flat' "
                    "(CLI: omit --search or use --search flat)."
                )
            session_dir = resume_from
        elif create_session:
            try:
                session_dir = create_session_dir(
                    problem=problem,
                    domain=self._domain(),
                    config=self.config,
                )
            except OSError as exc:
                logger.warning("Could not create session directory: %s", exc)

        result = proof_search.solve(
            problem,
            config=self.config,
            search_config=self.config.search or SearchConfig(),
            domain=self._domain(),
            client=self.client,
            ledger=ledger,
            balanced=balanced,
            session_dir=session_dir,
            resume_from=resume_from,
        )
        result.elapsed_seconds = time.time() - start_time
        result.session_dir = session_dir

        # Record final status unless search already checkpointed (exhaustion).
        if session_dir is not None and result.checkpoint_path is None:
            try:
                write_tree_checkpoint(
                    session_dir,
                    graph_dict=None,
                    # bridge_index doubles as a "bridges used" count here;
                    # its "resume-at" meaning is moot — completed sessions
                    # refuse resume regardless of this value.
                    bridge_index=result.iterations_used,
                    bridge_confidence=result.confidence,
                    failed_bridges=result.failed_approaches,
                    gap_states={},
                    atom_confs={},
                    best_confidence=result.confidence,
                    best_solution_text=result.solution,
                    token_ledger=ledger,
                    total_revisions=result.total_revisions,
                    status="solved" if result.solved else "unsolved",
                    max_bridges=(self.config.search or SearchConfig()).max_bridges,
                    problem=problem,
                )
            except CheckpointError as exc:
                logger.warning("Could not write final tree state: %s", exc)
        return result

    def _log(self, message: str) -> None:
        if self.config.verbose:
            print(message)
