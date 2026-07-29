"""v3.8 Alien-style technique enumeration for the exploration layer.

For each gap in the proof graph, the search layer asks: "Given the left
anchor (an established intermediate result) and the right anchor (the
target the gap must reach), what mathematical / physical techniques
could bridge the gap?" This module answers that question via a single
LLM call, parses the response into structured ``Technique`` records,
and filters out anything the search layer has already tried.

The output of ``enumerate_techniques`` is a list of *novel* techniques
(each with a name and a coherence score in [0, 1]). The search layer
combines coherence with a novelty prior to drive PUCT-based technique
selection — see spec §Alien Exploration (lines 231-241) and §Technique
Selection (lines 220-228).

Design notes
------------
- One LLM call per gap-exploration round. ``technique_budget`` (set by
  the preset) bounds how many enumeration rounds a gap can trigger
  before the search layer re-bridges. The explorer itself doesn't track
  that; it just produces the next batch when called.
- Calls ``subagents._call_model`` directly rather than going through
  ``generate()``. The explorer output is structured data (technique
  list), not a ``Solution`` — wrapping it in a ``Solution`` shape and
  then re-parsing would add a round-trip with no benefit.
- ``_safe_format`` is used (not ``str.format``) because anchor / problem
  text frequently contains literal ``{`` / ``}`` from LaTeX (e.g.,
  ``\\frac{a}{b}``, ``\\sum_{i=0}^{n}``). Naive ``.format`` blows up on
  those. ``_safe_format`` leaves unknown placeholders untouched, which
  is the right behavior for one-shot rendering with a closed key set.
- Parser is ordinal-indexed: ``TECHNIQUE N: ...`` and ``COHERENCE N: ...``
  are matched separately and joined on the integer ``N``. This survives
  interleaved prose, reordered lines, and dropped coherence entries
  (missing coherence → neutral 0.5 prior so the search layer still has
  the option to try the technique).

See ``docs/superpowers/specs/2026-04-11-v3.8-tree-search-design.md``
§Exploration Layer (lines 25-29, 220-241).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from alethic.subagents import _call_model, _safe_format

if TYPE_CHECKING:
    from collections.abc import Sequence

    from alethic.models import AgentConfig, TokenLedger

logger = logging.getLogger("alethic")


# ── Prompt templates ──────────────────────────────────────────────────────


EXPLORER_SYSTEM_MATH = """\
You are a mathematician brainstorming bridging techniques for a single \
gap in a longer proof. You receive an established left-anchor step, a \
target right-anchor step, the original problem, and a list of techniques \
that have already been tried and failed for this gap.

Your job is to propose 3-5 distinct mathematical techniques or lemmas \
that could bridge the gap, with a coherence rating (your confidence that \
each technique would actually work). You are NOT solving the gap — only \
enumerating candidate techniques for the search layer to attempt.
"""

EXPLORER_SYSTEM_PHYSICS = """\
You are a physicist brainstorming bridging techniques for a single gap \
in a longer derivation. You receive an established left-anchor step, a \
target right-anchor step, the original problem, and a list of techniques \
that have already been tried and failed for this gap.

Your job is to propose 3-5 distinct physical techniques, approximations, \
or named results that could bridge the gap, with a coherence rating \
(your confidence that each technique would actually work). You are NOT \
solving the gap — only enumerating candidate techniques for the search \
layer to attempt.
"""

EXPLORER_USER = """\
You are enumerating candidate bridging techniques for a single gap in \
a multi-step argument.

ORIGINAL PROBLEM:
{problem}

ESTABLISHED (left anchor — assume as given):
{left_anchor}

TARGET (right anchor — your suggested techniques should each, if \
successful, enable bridging to this):
{right_anchor}

ALREADY TRIED AND FAILED (do NOT propose these again):
{tried_techniques}

YOUR TASK
=========
List 3-5 distinct candidate techniques that could bridge the left anchor \
to the right anchor. Each technique should be a short, canonical name or \
description (e.g. "induction on n", "integration by parts with \
u=ln(x)", "Cauchy-Schwarz inequality", "dimensional analysis"). Avoid \
re-proposing anything in the ALREADY TRIED list.

For each technique, rate your coherence — your subjective probability \
that this technique would successfully bridge the gap, on a scale from \
0.0 (very unlikely) to 1.0 (very likely).

OUTPUT FORMAT
=============
Use exactly this format, one technique per pair, numbered consecutively:

TECHNIQUE 1: <short canonical name>
COHERENCE 1: <0.0 to 1.0>
TECHNIQUE 2: <short canonical name>
COHERENCE 2: <0.0 to 1.0>
TECHNIQUE 3: <short canonical name>
COHERENCE 3: <0.0 to 1.0>
...

Do not include any other prose between the pairs. If you genuinely \
cannot think of any candidate technique, output nothing at all rather \
than fabricating.
"""


# ── Data types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Technique:
    """A single candidate bridging technique with the model's coherence prior.

    ``coherence`` is the LLM-estimated probability (0.0 - 1.0) that this
    technique would successfully bridge the gap if attempted. The search
    layer combines it with a novelty score to drive PUCT-based selection
    — see spec §Technique Selection (lines 220-228).
    """

    name: str
    coherence: float


# ── Helpers ───────────────────────────────────────────────────────────────


_B = r"\*{0,2}"  # 0-2 asterisks before/after the label, same convention as subagents.py

_TECHNIQUE_LINE_RE = re.compile(
    rf"^[ \t]*{_B}TECHNIQUE[ \t]+(\d+):{_B}[ \t]*(.*?)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_COHERENCE_LINE_RE = re.compile(
    rf"^[ \t]*{_B}COHERENCE[ \t]+(\d+):{_B}[ \t]*(\S+)",
    re.MULTILINE | re.IGNORECASE,
)

_NONE_MARKER = "(none yet — propose any technique that could work)"
_MISSING_COHERENCE_DEFAULT = 0.5


def _format_tried(tried: Sequence[str]) -> str:
    """Render the ``tried_techniques`` list for the prompt.

    Empty list → ``"(none yet ...)"`` marker so the LLM has visible
    content there instead of an empty section that may confuse it. Each
    non-empty item is rendered on its own line, prefixed with "- ", in
    the order given.
    """
    if not tried:
        return _NONE_MARKER
    return "\n".join(f"- {item}" for item in tried)


def _parse_techniques(text: str) -> list[Technique]:
    """Extract ``Technique`` records from explorer LLM output.

    Strategy: collect ``(ordinal, name)`` and ``(ordinal, coherence)``
    pairs independently, then join on ordinal. Missing coherence for a
    given ordinal → neutral 0.5 prior. Coherence parse failure → 0.5.
    Out-of-range coherence → clamped to [0, 1]. Empty/whitespace-only
    technique names → dropped. Stray COHERENCE lines without a matching
    TECHNIQUE → ignored.
    """
    technique_lines = _TECHNIQUE_LINE_RE.findall(text)
    coherence_lines = _COHERENCE_LINE_RE.findall(text)

    # ordinal → name (later duplicates of the same ordinal win — last-write-wins
    # is fine, the LLM shouldn't repeat ordinals)
    name_by_ord: dict[int, str] = {}
    for ord_str, name in technique_lines:
        name_by_ord[int(ord_str)] = name.strip()

    coh_by_ord: dict[int, float] = {}
    for ord_str, raw_coh in coherence_lines:
        try:
            value = float(raw_coh)
        except ValueError:
            value = _MISSING_COHERENCE_DEFAULT
        coh_by_ord[int(ord_str)] = max(0.0, min(1.0, value))

    result: list[Technique] = []
    for ordinal in sorted(name_by_ord):
        name = name_by_ord[ordinal]
        if not name:
            continue  # drop empty names (e.g. "TECHNIQUE 1: " with nothing after)
        coherence = coh_by_ord.get(ordinal, _MISSING_COHERENCE_DEFAULT)
        result.append(Technique(name=name, coherence=coherence))
    return result


def _filter_novel(techniques: Sequence[Technique], *, tried: Sequence[str]) -> list[Technique]:
    """Drop techniques whose normalized name is in ``tried`` or already kept.

    Normalization is case-insensitive plus surrounding-whitespace strip,
    so ``"  Induction "`` matches ``"induction"``. Input order is
    preserved. Intra-call duplicates (same normalized name twice in
    ``techniques``) keep the first occurrence.
    """
    def _norm(s: str) -> str:
        return s.strip().casefold()

    seen: set[str] = {_norm(t) for t in tried}
    out: list[Technique] = []
    for tech in techniques:
        key = _norm(tech.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(tech)
    return out


def _select_system_prompt(domain: str) -> str:
    """Math is the default; physics is the explicit override.

    Matches the convention in ``microkernel._select_system_prompts``.
    """
    if domain == "physics":
        return EXPLORER_SYSTEM_PHYSICS
    return EXPLORER_SYSTEM_MATH


# ── Public entry point ────────────────────────────────────────────────────


def enumerate_techniques(
    *,
    left_anchor: str,
    right_anchor: str,
    tried_techniques: Sequence[str],
    problem_context: str,
    config: AgentConfig,
    domain: str,
    client,
    ledger: TokenLedger | None = None,
) -> list[Technique]:
    """Ask the model for candidate bridging techniques between two anchors.

    Returns techniques the search layer hasn't already tried (filtered
    case-insensitively). Empty list is a valid return — it means the LLM
    didn't propose anything novel this round; the search layer should
    treat the gap's exploration budget as exhausted.

    Uses ``config.temperature_generator`` (exploration wants diversity,
    not the verifier's low-temperature determinism). Extended thinking
    is honored if enabled in ``config``.
    """
    system = _select_system_prompt(domain)
    user_message = _safe_format(
        EXPLORER_USER,
        problem=problem_context,
        left_anchor=left_anchor,
        right_anchor=right_anchor,
        tried_techniques=_format_tried(tried_techniques),
    )

    logger.info(
        "Explorer: enumerating techniques (domain=%s, tried=%d)",
        domain, len(tried_techniques),
    )

    response_text = _call_model(
        client,
        system=system,
        user_message=user_message,
        config=config,
        temperature=config.temperature_generator,
        ledger=ledger,
    )

    parsed = _parse_techniques(response_text)
    novel = _filter_novel(parsed, tried=tried_techniques)
    logger.info(
        "Explorer: parsed=%d, novel=%d (after filter against tried)",
        len(parsed), len(novel),
    )
    return novel
