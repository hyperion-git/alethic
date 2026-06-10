"""v3.8 GVR microkernel — atom-scoped generate / verify / revise.

A microkernel call takes a single ``MicrokernelTask`` (one gap between two
established anchors, plus a chosen bridging technique) and runs the
existing ``alethic.subagents`` Generate → Verify → Revise loop scoped to
that gap. The result is a ``MicrokernelResult`` whose ``status`` is one
of:

- ``"filled"``  — verifier accepted the candidate atom; ``replacement_content``
  is the atom body (header-stripped).
- ``"too_large"`` — either the generator/reviser explicitly emitted
  ``GAP TOO LARGE``, or the verifier's critique contains complexity hints
  (e.g. "several steps", "non-trivial intermediate"). Search layer should
  subdivide rather than retry.
- ``"failed"`` — revision budget exhausted without acceptance and without
  a too-large signal. Search layer can try a different technique or count
  this toward subdivision via failure-count threshold.

Design notes
------------
- The microkernel reuses ``generate()`` / ``verify()`` / ``revise()`` from
  ``subagents.py`` via their ``system_prompt`` / ``user_template`` override
  hooks. No changes to the existing subagent API. Atom-specific
  placeholders (``left_anchor``, ``right_anchor``, ``technique``) are
  pre-rendered into the templates before the subagent call; the
  ``{problem}`` placeholder stays open for the subagent's own pass to
  fill via ``_safe_format``.
- The atom output header is the literal ``ATOM[GAP]`` (not ``ATOM[N]``
  with a numeric ID). The gap's true ID is owned by the search layer,
  not the LLM — fixing the header removes a class of "model invents
  wrong ID" bugs and lets the extraction regex be bulletproof.
- ``balanced=False`` on the atom generator: the existing
  ``BALANCED_GENERATOR_ADDENDUM`` is counterexample-first anti-confirmation
  guidance. Between two established anchors there is nothing to
  counterexample — the only question is whether the chosen technique
  bridges the gap.
- The microkernel does not emit events. Event logging belongs to the
  search layer (one level up), which has the iteration/gap-id context
  needed to make events useful.

See ``docs/superpowers/specs/2026-04-11-v3.8-tree-search-design.md``
(§Layer 3 — GVR Microkernel, lines 30-43, 105-122, 161-202).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from alethic.error_taxonomy import classify_errors
from alethic.physics_prompts import PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM
from alethic.prompts import ADVERSARIAL_VERIFIER_ADDENDUM
from alethic.subagents import _safe_format, generate, revise, verify

if TYPE_CHECKING:
    from alethic.models import AgentConfig, OracleType, TokenLedger

logger = logging.getLogger("alethic")


# ── Atom-scoped prompt templates ──────────────────────────────────────────

ATOM_GENERATOR_SYSTEM_MATH = """\
You are a mathematical proof author working on a single intermediate step \
(an "atom") of a longer proof. The proof has already established a left-anchor \
step; your job is to produce the single atom that bridges the established \
left-anchor to a target right-anchor, using a specified bridging technique.

You are NOT writing the whole proof — only the one gap atom.
"""

ATOM_GENERATOR_SYSTEM_PHYSICS = """\
You are a physicist deriving a single intermediate step (an "atom") of a \
longer derivation. The derivation has already established a left-anchor step; \
your job is to produce the single atom that bridges the established \
left-anchor to a target right-anchor, using a specified bridging technique.

You are NOT writing the whole derivation — only the one gap atom.
"""

ATOM_GENERATOR_USER = """\
You are filling a single gap in a multi-step proof.

ORIGINAL PROBLEM:
{problem}

ESTABLISHED (left anchor — assume as given, do NOT re-derive):
{left_anchor}

TARGET (right anchor — your atom must enable reaching this):
{right_anchor}

BRIDGING TECHNIQUE: {technique}

YOUR TASK
=========
Produce a SINGLE atom of work that bridges the gap from the left anchor to \
the right anchor using the indicated technique. The atom is one logical / \
mathematical step, not a full sub-proof.

OUTPUT FORMAT
=============
Begin your response with EXACTLY one of:

(A) Single-atom answer — start with the literal header `ATOM[GAP]` on its \
own line, then the atom content beneath.

(B) Subdivision signal — if the gap CANNOT be bridged in one step (e.g. it \
requires multiple non-trivial intermediate results), respond with the literal \
phrase `GAP TOO LARGE` on its own line, followed by one sentence explaining \
what intermediate result would be needed.

Do not output both. Do not re-derive the anchors. Do not write a full proof.
"""

ATOM_VERIFIER_SYSTEM_MATH = """\
You are a rigorous verifier of a SINGLE atomic step in a longer mathematical \
proof. You receive the original problem, the left anchor (an established \
step), the right anchor (the target step), the chosen bridging technique, \
and a candidate atom that claims to bridge the gap.

Your job is to evaluate whether the candidate atom validly bridges the gap \
using the indicated technique. You are NOT evaluating the whole proof — only \
this one atom.
"""

ATOM_VERIFIER_SYSTEM_PHYSICS = """\
You are a rigorous verifier of a SINGLE atomic step in a longer physics \
derivation. You receive the original problem, the left anchor (an established \
step), the right anchor (the target step), the chosen bridging technique, \
and a candidate atom that claims to bridge the gap.

Your job is to evaluate whether the candidate atom validly bridges the gap \
using the indicated technique. You are NOT evaluating the whole derivation — \
only this one atom.
"""

ATOM_VERIFIER_USER = """\
Evaluate the following candidate atom for correctness, completeness, and \
proper use of the bridging technique.

ORIGINAL PROBLEM:
{problem}

ESTABLISHED (left anchor — treat as given):
{left_anchor}

TARGET (right anchor — atom must enable reaching this):
{right_anchor}

BRIDGING TECHNIQUE WAS: {technique}

CANDIDATE ATOM:
{solution}

CHECK
=====
1. Does the candidate atom validly derive from the left anchor (or from \
premises that the left anchor establishes)?
2. Does the candidate atom enable progress toward (or reach) the right \
anchor?
3. Was the bridging technique used as intended, or did the candidate solve \
the gap a different way?
4. Is the candidate a SINGLE step, or does it implicitly require multiple \
non-trivial intermediate sub-steps? If the latter, flag this — the gap may \
be too large for a single atom and should be subdivided.

OUTPUT FORMAT
=============
VERDICT: correct | minor_issues | major_flaw | unsolved
CONFIDENCE: 0.0 to 1.0
CRITIQUE: ... (detail any issues found)
ISSUES:
- [SEVERITY] (issue description)

If you believe the gap is too large for a single atom (i.e. the candidate \
implicitly contains multiple steps that should be made explicit), include \
the literal phrase `GAP TOO LARGE` somewhere in your CRITIQUE — this \
signals the search layer to subdivide rather than revise.
"""

ATOM_REVISER_SYSTEM = """\
You are revising a SINGLE atom of a longer proof based on verifier critique. \
You receive the original problem, the established left anchor, the target \
right anchor, the chosen bridging technique, the previous (flawed) candidate \
atom, and the verifier's critique. Produce an improved atom that addresses \
the critique while staying scoped to a SINGLE step.

You are NOT revising the whole proof — only this one atom.
"""

ATOM_REVISER_USER = """\
Revise the candidate atom below based on the verifier critique.

ORIGINAL PROBLEM:
{problem}

ESTABLISHED (left anchor):
{left_anchor}

TARGET (right anchor):
{right_anchor}

BRIDGING TECHNIQUE: {technique}

PREVIOUS CANDIDATE ATOM:
{solution}

VERIFIER CRITIQUE:
{critique}

SPECIFIC ISSUES:
{issues}

OUTPUT FORMAT
=============
Begin your response with the literal header `ATOM[GAP]` on its own line, \
then the revised atom content beneath. Stay scoped to a SINGLE atom — do \
not expand into multiple sub-steps. If the gap genuinely requires multiple \
sub-steps, respond instead with `GAP TOO LARGE` on its own line followed \
by a one-sentence justification.
"""


# ── Data types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MicrokernelTask:
    """Input to the GVR microkernel — one gap to fill, with context.

    Frozen so the search layer can safely keep references and the
    microkernel cannot mutate its input. ``gap_id`` is the ``AtomNode.id``
    of the failed atom in the search layer's ``ProofGraph``.

    ``oracle``/``force_adversarial`` carry the ``_ORACLE_ROUTING`` verdict for
    retry attempts (v3.8 integration). Only ``force_adversarial`` is
    actionable here — the verification-ladder layers L0–L2 are embedded
    checks the microkernel cannot toggle per call.
    """

    gap_id: int
    left_anchor: str
    right_anchor: str
    technique: str
    problem_context: str
    max_revisions: int
    oracle: OracleType | None = None        # advisory routing target from _ORACLE_ROUTING; logged only
    force_adversarial: bool = False         # inject adversarial verifier addendum into verify calls


@dataclass(frozen=True)
class MicrokernelResult:
    """Output of one microkernel attempt at filling a gap.

    ``replacement_content`` is the atom body without the ``ATOM[GAP]``
    header — search layer stores this directly into ``AtomNode.content``
    when ``status == "filled"``. For ``failed`` and ``too_large`` it
    holds the last candidate's text (header-stripped if any), useful
    for debugging logs. ``revisions_used`` counts the number of revision
    rounds executed; 0 means the first generation already terminated
    the loop.
    """

    status: Literal["filled", "failed", "too_large"]
    replacement_content: str
    confidence: float
    critique: str
    error_category: str
    revisions_used: int = 0


# ── Helpers ───────────────────────────────────────────────────────────────


_ATOM_HEADER_RE = re.compile(r"^\s*ATOM\[GAP\]\s*$", re.MULTILINE)
_TOO_LARGE_RE = re.compile(r"\bGAP\s+TOO\s+LARGE\b", re.IGNORECASE)

# Keyword hints from the verifier's critique that the gap is too wide for a
# single atom. These are deliberately broad — the search layer's failure-
# count threshold catches the cases where the verifier doesn't say so
# explicitly but the gap still resists every technique tried.
_TOO_LARGE_CRITIQUE_HINTS: tuple[str, ...] = (
    "several steps",
    "multiple steps",
    "non-trivial intermediate",
    "needs intermediate",
    "needs an intermediate",
    "should be subdivided",
    "should subdivide",
    "too wide",
    "too large for a single",
    "gap is too large",
    "split into",
    "split this into",
)


def _detect_too_large(text: str) -> bool:
    """True if ``text`` contains an explicit or hinted too-large signal."""
    if _TOO_LARGE_RE.search(text):
        return True
    lowered = text.lower()
    return any(hint in lowered for hint in _TOO_LARGE_CRITIQUE_HINTS)


def _extract_atom_content(solution_text: str) -> str:
    """Strip the ``ATOM[GAP]`` header (if present) and surrounding whitespace.

    Generator and reviser output begin with the literal ``ATOM[GAP]``
    header per the user templates. The body stored in
    ``replacement_content`` is everything after that header. If the header
    is absent (model didn't follow instructions exactly), returns the
    stripped text as-is — letting the search layer decide how to handle
    it instead of silently dropping content.
    """
    text = solution_text.strip()
    header_match = _ATOM_HEADER_RE.search(text)
    if header_match:
        text = text[header_match.end():].lstrip()
    return text


def _render_atom_template(
    template: str,
    *,
    left_anchor: str,
    right_anchor: str,
    technique: str,
) -> str:
    """Pre-render atom-specific placeholders.

    Leaves ``{problem}``, ``{solution}``, ``{critique}``, ``{issues}``
    intact for the downstream subagent call to fill via its own
    ``_safe_format`` pass. ``_safe_format`` leaves unknown placeholders
    alone (returns the original ``{name}`` for unmatched keys), so this
    two-pass rendering is safe.
    """
    return _safe_format(
        template,
        left_anchor=left_anchor,
        right_anchor=right_anchor,
        technique=technique,
    )


def _select_system_prompts(domain: str) -> tuple[str, str, str]:
    """Return ``(generator_system, verifier_system, reviser_system)``.

    Unknown domains fall through to the math prompts — math is the
    canonical pattern and physics is the explicit override. The reviser
    system prompt is domain-agnostic.
    """
    if domain == "physics":
        return (
            ATOM_GENERATOR_SYSTEM_PHYSICS,
            ATOM_VERIFIER_SYSTEM_PHYSICS,
            ATOM_REVISER_SYSTEM,
        )
    return (
        ATOM_GENERATOR_SYSTEM_MATH,
        ATOM_VERIFIER_SYSTEM_MATH,
        ATOM_REVISER_SYSTEM,
    )


# ── Public entry point ────────────────────────────────────────────────────


def gvr_microkernel(
    task: MicrokernelTask,
    *,
    config: AgentConfig,
    domain: str,
    client,
    ledger: TokenLedger | None = None,
) -> MicrokernelResult:
    """Run atom-scoped Generate → Verify → Revise on a single gap.

    Sequence: one generation, one verification, then up to
    ``task.max_revisions`` revise→verify cycles. The loop exits early on:

    - ``filled``: verifier accepts the candidate (verdict acceptable,
      confidence ≥ ``config.confidence_threshold``).
    - ``too_large``: explicit ``GAP TOO LARGE`` text from generator or
      reviser, OR keyword hint detected in verifier critique.
    - ``failed``: budget exhausted with neither acceptance nor too-large.

    The microkernel does not propagate context-exhaustion errors back to
    the caller — those bubble up as ``ContextExhaustedError`` / ``TruncatedResponseError``
    from the underlying subagent calls and the search layer is expected
    to handle them.
    """
    gen_system, ver_system, rev_system = _select_system_prompts(domain)

    extra_system: str | None = None
    if task.force_adversarial:
        extra_system = (
            PHYSICS_ADVERSARIAL_VERIFIER_ADDENDUM
            if domain == "physics"
            else ADVERSARIAL_VERIFIER_ADDENDUM
        )

    gen_user = _render_atom_template(
        ATOM_GENERATOR_USER,
        left_anchor=task.left_anchor,
        right_anchor=task.right_anchor,
        technique=task.technique,
    )
    ver_user = _render_atom_template(
        ATOM_VERIFIER_USER,
        left_anchor=task.left_anchor,
        right_anchor=task.right_anchor,
        technique=task.technique,
    )
    rev_user = _render_atom_template(
        ATOM_REVISER_USER,
        left_anchor=task.left_anchor,
        right_anchor=task.right_anchor,
        technique=task.technique,
    )

    logger.info(
        "Microkernel: starting gap_id=%d technique=%r max_revisions=%d",
        task.gap_id, task.technique, task.max_revisions,
    )

    # ── Phase 1: Generate ──────────────────────────────────────────────
    candidate = generate(
        client,
        problem=task.problem_context,
        config=config,
        iteration=0,
        balanced=False,
        system_prompt=gen_system,
        user_template=gen_user,
        ledger=ledger,
    )

    if _TOO_LARGE_RE.search(candidate.solution_text):
        logger.info("Microkernel: generator emitted GAP TOO LARGE")
        return MicrokernelResult(
            status="too_large",
            replacement_content="",
            confidence=0.0,
            critique=candidate.solution_text,
            error_category="too_large",
            revisions_used=0,
        )

    # ── Phase 2: Verify ────────────────────────────────────────────────
    result = verify(
        client,
        problem=task.problem_context,
        solution=candidate,
        config=config,
        system_prompt=ver_system,
        user_template=ver_user,
        ledger=ledger,
        extra_system=extra_system,
    )

    if result.is_acceptable(config.confidence_threshold):
        logger.info(
            "Microkernel: gap filled on first try (verdict=%s confidence=%.2f)",
            result.verdict.value, result.confidence,
        )
        return MicrokernelResult(
            status="filled",
            replacement_content=_extract_atom_content(candidate.solution_text),
            confidence=result.confidence,
            critique=result.critique,
            error_category=classify_errors(result.critique),
            revisions_used=0,
        )

    if _detect_too_large(result.critique):
        logger.info("Microkernel: verifier hints gap is too large (no revision)")
        return MicrokernelResult(
            status="too_large",
            replacement_content="",
            confidence=result.confidence,
            critique=result.critique,
            error_category="too_large",
            revisions_used=0,
        )

    # ── Phase 3: Revision loop ────────────────────────────────────────
    for rev_num in range(1, task.max_revisions + 1):
        candidate = revise(
            client,
            problem=task.problem_context,
            solution=candidate,
            verification=result,
            config=config,
            revision_number=rev_num,
            system_prompt=rev_system,
            user_template=rev_user,
            ledger=ledger,
        )

        if _TOO_LARGE_RE.search(candidate.solution_text):
            logger.info(
                "Microkernel: reviser emitted GAP TOO LARGE on revision %d",
                rev_num,
            )
            return MicrokernelResult(
                status="too_large",
                replacement_content="",
                confidence=0.0,
                critique=candidate.solution_text,
                error_category="too_large",
                revisions_used=rev_num,
            )

        result = verify(
            client,
            problem=task.problem_context,
            solution=candidate,
            config=config,
            system_prompt=ver_system,
            user_template=ver_user,
            ledger=ledger,
            extra_system=extra_system,
        )

        if result.is_acceptable(config.confidence_threshold):
            logger.info(
                "Microkernel: gap filled after %d revision(s) (verdict=%s confidence=%.2f)",
                rev_num, result.verdict.value, result.confidence,
            )
            return MicrokernelResult(
                status="filled",
                replacement_content=_extract_atom_content(candidate.solution_text),
                confidence=result.confidence,
                critique=result.critique,
                error_category=classify_errors(result.critique),
                revisions_used=rev_num,
            )

        if _detect_too_large(result.critique):
            logger.info(
                "Microkernel: verifier hints too-large on revision %d", rev_num,
            )
            return MicrokernelResult(
                status="too_large",
                replacement_content="",
                confidence=result.confidence,
                critique=result.critique,
                error_category="too_large",
                revisions_used=rev_num,
            )

    # ── Budget exhausted ──────────────────────────────────────────────
    logger.info(
        "Microkernel: gap failed after %d revision(s) (final confidence=%.2f)",
        task.max_revisions, result.confidence,
    )
    return MicrokernelResult(
        status="failed",
        replacement_content=_extract_atom_content(candidate.solution_text),
        confidence=result.confidence,
        critique=result.critique,
        error_category=classify_errors(result.critique),
        revisions_used=task.max_revisions,
    )
