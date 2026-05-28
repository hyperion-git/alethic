"""Tests for src/alethic/microkernel.py (v3.8 atom-scoped GVR)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alethic import microkernel as mk
from alethic.microkernel import (
    ATOM_GENERATOR_SYSTEM_MATH,
    ATOM_GENERATOR_SYSTEM_PHYSICS,
    ATOM_GENERATOR_USER,
    ATOM_REVISER_SYSTEM,
    ATOM_REVISER_USER,
    ATOM_VERIFIER_SYSTEM_MATH,
    ATOM_VERIFIER_SYSTEM_PHYSICS,
    ATOM_VERIFIER_USER,
    MicrokernelResult,
    MicrokernelTask,
    _detect_too_large,
    _extract_atom_content,
    _render_atom_template,
    _select_system_prompts,
    gvr_microkernel,
)
from alethic.models import AgentConfig, Issue, IssueSeverity, Solution, Verdict, VerificationResult

# ──────────────────────────────────────────────────────────────────────────
# Data type construction
# ──────────────────────────────────────────────────────────────────────────


class TestDataTypes:
    def test_task_construction(self):
        task = MicrokernelTask(
            gap_id=42,
            left_anchor="L",
            right_anchor="R",
            technique="induction",
            problem_context="P",
            max_revisions=2,
        )
        assert task.gap_id == 42
        assert task.technique == "induction"

    def test_task_is_frozen(self):
        task = MicrokernelTask(
            gap_id=1, left_anchor="", right_anchor="", technique="t",
            problem_context="", max_revisions=0,
        )
        with pytest.raises(AttributeError):
            task.gap_id = 99  # type: ignore[misc]

    def test_result_construction_filled(self):
        r = MicrokernelResult(
            status="filled", replacement_content="atom body",
            confidence=0.95, critique="ok", error_category="none",
            revisions_used=1,
        )
        assert r.status == "filled"
        assert r.revisions_used == 1

    def test_result_default_revisions_used_zero(self):
        r = MicrokernelResult(
            status="filled", replacement_content="x",
            confidence=0.9, critique="", error_category="",
        )
        assert r.revisions_used == 0

    def test_result_is_frozen(self):
        r = MicrokernelResult(
            status="filled", replacement_content="", confidence=0.0,
            critique="", error_category="",
        )
        with pytest.raises(AttributeError):
            r.status = "failed"  # type: ignore[misc]


# ──────────────────────────────────────────────────────────────────────────
# _detect_too_large
# ──────────────────────────────────────────────────────────────────────────


class TestDetectTooLarge:
    def test_explicit_signal(self):
        assert _detect_too_large("GAP TOO LARGE") is True

    def test_explicit_signal_case_insensitive(self):
        assert _detect_too_large("gap too large") is True
        assert _detect_too_large("Gap Too Large") is True

    def test_explicit_signal_embedded(self):
        assert _detect_too_large("Looks like a GAP TOO LARGE situation here.") is True

    @pytest.mark.parametrize("hint", [
        "needs several steps",
        "multiple steps required",
        "this involves a non-trivial intermediate result",
        "needs an intermediate lemma",
        "should be subdivided",
        "should subdivide",
        "the gap is too wide",
        "too large for a single atom",
        "the gap is too large to fill in one step",
        "split into two atoms",
        "split this into smaller steps",
    ])
    def test_keyword_hints(self, hint: str):
        assert _detect_too_large(hint) is True

    def test_negative_no_signal(self):
        assert _detect_too_large(
            "The atom validly bridges the gap using the indicated technique."
        ) is False

    def test_negative_empty(self):
        assert _detect_too_large("") is False

    def test_partial_word_does_not_trigger(self):
        """`too_large` (underscore) shouldn't trip the keyword scan."""
        assert _detect_too_large("variable named too_large_threshold") is False


# ──────────────────────────────────────────────────────────────────────────
# _extract_atom_content
# ──────────────────────────────────────────────────────────────────────────


class TestExtractAtomContent:
    def test_strips_header_at_start(self):
        text = "ATOM[GAP]\nThis is the atom body."
        assert _extract_atom_content(text) == "This is the atom body."

    def test_strips_header_with_leading_whitespace(self):
        text = "\n\n  ATOM[GAP]\nThe atom."
        assert _extract_atom_content(text) == "The atom."

    def test_strips_header_after_preamble(self):
        """If the model wraps with a brief intro, still find the header."""
        text = "Here is my answer:\n\nATOM[GAP]\nThe real content."
        assert _extract_atom_content(text) == "The real content."

    def test_no_header_returns_stripped_text(self):
        text = "  The atom body without a header.  "
        assert _extract_atom_content(text) == "The atom body without a header."

    def test_empty_returns_empty(self):
        assert _extract_atom_content("") == ""

    def test_only_header_returns_empty(self):
        assert _extract_atom_content("ATOM[GAP]\n") == ""

    def test_preserves_multiline_body(self):
        text = "ATOM[GAP]\nLine 1\nLine 2\nLine 3"
        assert _extract_atom_content(text) == "Line 1\nLine 2\nLine 3"


# ──────────────────────────────────────────────────────────────────────────
# _render_atom_template
# ──────────────────────────────────────────────────────────────────────────


class TestRenderAtomTemplate:
    def test_substitutes_atom_placeholders(self):
        out = _render_atom_template(
            "L={left_anchor} R={right_anchor} T={technique}",
            left_anchor="alpha", right_anchor="omega", technique="induction",
        )
        assert out == "L=alpha R=omega T=induction"

    def test_leaves_problem_placeholder_for_downstream(self):
        """The whole point: {problem} must survive pre-rendering."""
        out = _render_atom_template(
            "{problem} L={left_anchor}",
            left_anchor="L", right_anchor="R", technique="T",
        )
        assert out == "{problem} L=L"

    def test_leaves_solution_and_critique_placeholders(self):
        """Reviser/verifier templates have {solution}, {critique}, {issues}."""
        out = _render_atom_template(
            "{solution} {critique} {issues} L={left_anchor}",
            left_anchor="L", right_anchor="R", technique="T",
        )
        assert out == "{solution} {critique} {issues} L=L"


# ──────────────────────────────────────────────────────────────────────────
# _select_system_prompts
# ──────────────────────────────────────────────────────────────────────────


class TestSelectSystemPrompts:
    def test_math_domain(self):
        gen, ver, rev = _select_system_prompts("math")
        assert gen == ATOM_GENERATOR_SYSTEM_MATH
        assert ver == ATOM_VERIFIER_SYSTEM_MATH
        assert rev == ATOM_REVISER_SYSTEM

    def test_physics_domain(self):
        gen, ver, rev = _select_system_prompts("physics")
        assert gen == ATOM_GENERATOR_SYSTEM_PHYSICS
        assert ver == ATOM_VERIFIER_SYSTEM_PHYSICS
        assert rev == ATOM_REVISER_SYSTEM

    def test_unknown_domain_defaults_to_math(self):
        gen, ver, rev = _select_system_prompts("biology")
        assert gen == ATOM_GENERATOR_SYSTEM_MATH


# ──────────────────────────────────────────────────────────────────────────
# Prompt template sanity
# ──────────────────────────────────────────────────────────────────────────


class TestPromptTemplates:
    def test_generator_user_has_all_placeholders(self):
        for p in ("{problem}", "{left_anchor}", "{right_anchor}", "{technique}"):
            assert p in ATOM_GENERATOR_USER, f"missing {p} in ATOM_GENERATOR_USER"

    def test_verifier_user_has_all_placeholders(self):
        for p in (
            "{problem}", "{left_anchor}", "{right_anchor}",
            "{technique}", "{solution}",
        ):
            assert p in ATOM_VERIFIER_USER, f"missing {p} in ATOM_VERIFIER_USER"

    def test_reviser_user_has_all_placeholders(self):
        for p in (
            "{problem}", "{left_anchor}", "{right_anchor}", "{technique}",
            "{solution}", "{critique}", "{issues}",
        ):
            assert p in ATOM_REVISER_USER, f"missing {p} in ATOM_REVISER_USER"

    def test_generator_user_mentions_gap_too_large(self):
        assert "GAP TOO LARGE" in ATOM_GENERATOR_USER

    def test_reviser_user_mentions_gap_too_large(self):
        assert "GAP TOO LARGE" in ATOM_REVISER_USER

    def test_verifier_user_mentions_gap_too_large(self):
        assert "GAP TOO LARGE" in ATOM_VERIFIER_USER


# ──────────────────────────────────────────────────────────────────────────
# gvr_microkernel — end-to-end with mocked subagents
# ──────────────────────────────────────────────────────────────────────────


def _make_solution(text: str) -> Solution:
    return Solution(problem="P", solution_text=text, iteration=0)


def _make_verification(
    *,
    verdict: Verdict = Verdict.CORRECT,
    confidence: float = 0.95,
    critique: str = "looks good",
    issues: list[Issue] | None = None,
) -> VerificationResult:
    return VerificationResult(
        verdict=verdict,
        critique=critique,
        confidence=confidence,
        issues=issues or [],
    )


def _make_config(
    *,
    confidence_threshold: float = 0.90,
) -> AgentConfig:
    """Construct a minimal AgentConfig for microkernel tests.

    AgentConfig has many fields; the microkernel only reads
    ``confidence_threshold`` and ``model`` (via the subagent calls,
    which are mocked here). The full-default constructor works because
    AgentConfig has defaults for every field.
    """
    return AgentConfig(confidence_threshold=confidence_threshold)


@pytest.fixture
def task() -> MicrokernelTask:
    return MicrokernelTask(
        gap_id=7, left_anchor="L", right_anchor="R", technique="induction",
        problem_context="prove P", max_revisions=2,
    )


@pytest.fixture
def mocked_subagents(monkeypatch):
    """Patch generate/verify/revise on the microkernel module."""
    gen_mock = MagicMock()
    ver_mock = MagicMock()
    rev_mock = MagicMock()
    monkeypatch.setattr(mk, "generate", gen_mock)
    monkeypatch.setattr(mk, "verify", ver_mock)
    monkeypatch.setattr(mk, "revise", rev_mock)
    return gen_mock, ver_mock, rev_mock


class TestGvrMicrokernel:
    def test_filled_on_first_try(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nthe atom body")
        ver.return_value = _make_verification(confidence=0.95)

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "filled"
        assert result.replacement_content == "the atom body"
        assert result.confidence == 0.95
        assert result.revisions_used == 0
        assert rev.call_count == 0

    def test_generator_signals_too_large(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution(
            "GAP TOO LARGE\nneed an intermediate lemma about parity."
        )

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "too_large"
        assert result.error_category == "too_large"
        assert ver.call_count == 0
        assert rev.call_count == 0

    def test_verifier_hints_too_large_no_revision(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification(
            verdict=Verdict.MAJOR_FLAW,
            confidence=0.4,
            critique="The candidate skips several steps that should be made explicit.",
        )

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "too_large"
        assert rev.call_count == 0, "should not revise when verifier hints too-large"

    def test_successful_revision_after_one_failure(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nfirst attempt")
        # First verify fails (with no too-large hint), second succeeds
        ver.side_effect = [
            _make_verification(
                verdict=Verdict.MAJOR_FLAW, confidence=0.3,
                critique="off-by-one in the inductive step",
            ),
            _make_verification(verdict=Verdict.CORRECT, confidence=0.96),
        ]
        rev.return_value = _make_solution("ATOM[GAP]\ncorrected body")

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "filled"
        assert result.replacement_content == "corrected body"
        assert result.revisions_used == 1
        assert rev.call_count == 1

    def test_budget_exhausted(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\ntry 0")
        # All verifies fail, no too-large hint
        ver.return_value = _make_verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.2,
            critique="incorrect derivation, no specific complexity signal",
        )
        rev.side_effect = [
            _make_solution("ATOM[GAP]\ntry 1"),
            _make_solution("ATOM[GAP]\ntry 2"),
        ]

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "failed"
        assert result.revisions_used == 2  # task fixture has max_revisions=2
        # Last revision's content is preserved (header-stripped) for debugging
        assert result.replacement_content == "try 2"
        # Subagent call counts: 1 generate + 1+2 verifies + 2 revises
        assert ver.call_count == 3
        assert rev.call_count == 2

    def test_reviser_emits_too_large(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\ntry 0")
        ver.return_value = _make_verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.2,
            critique="wrong algebra, no complexity hint",
        )
        # Reviser gives up after seeing the critique
        rev.return_value = _make_solution("GAP TOO LARGE\nrequires a sub-lemma")

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "too_large"
        assert result.revisions_used == 1
        # Verifier should not be called again after the reviser's signal
        assert ver.call_count == 1

    def test_zero_max_revisions_no_revise_phase(self, mocked_subagents):
        """task.max_revisions=0 → generate, verify, return without revising."""
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification(
            verdict=Verdict.MAJOR_FLAW, confidence=0.3,
            critique="wrong but no complexity hint",
        )
        task = MicrokernelTask(
            gap_id=1, left_anchor="L", right_anchor="R", technique="T",
            problem_context="P", max_revisions=0,
        )

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "failed"
        assert result.revisions_used == 0
        assert rev.call_count == 0

    def test_confidence_below_threshold_treated_as_failed(self, task, mocked_subagents):
        """is_acceptable requires verdict=CORRECT AND confidence >= threshold."""
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        # CORRECT verdict but confidence just below the 0.90 threshold
        ver.return_value = _make_verification(
            verdict=Verdict.CORRECT, confidence=0.85,
            critique="basically correct, some doubt",
        )
        # No too-large hint in critique → goes into revision loop
        rev.return_value = _make_solution("ATOM[GAP]\nrevised")
        # Make the post-revise verify pass:
        ver.side_effect = [
            _make_verification(verdict=Verdict.CORRECT, confidence=0.85,
                                critique="correct, some doubt"),
            _make_verification(verdict=Verdict.CORRECT, confidence=0.92),
        ]

        result = gvr_microkernel(
            task, config=_make_config(confidence_threshold=0.90),
            domain="math", client=MagicMock(),
        )

        assert result.status == "filled"
        assert result.revisions_used == 1

    def test_physics_domain_uses_physics_system_prompts(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification()

        gvr_microkernel(
            task, config=_make_config(), domain="physics", client=MagicMock(),
        )

        # generate() was called with system_prompt=physics version
        _, gen_kwargs = gen.call_args
        assert gen_kwargs["system_prompt"] == ATOM_GENERATOR_SYSTEM_PHYSICS
        # verify() was called with verifier physics version
        _, ver_kwargs = ver.call_args
        assert ver_kwargs["system_prompt"] == ATOM_VERIFIER_SYSTEM_PHYSICS

    def test_math_domain_uses_math_system_prompts(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification()

        gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        _, gen_kwargs = gen.call_args
        assert gen_kwargs["system_prompt"] == ATOM_GENERATOR_SYSTEM_MATH

    def test_atom_specific_placeholders_prerendered(self, task, mocked_subagents):
        """Generator user_template must have left_anchor/right_anchor/technique
        already substituted before reaching generate() — otherwise generate()'s
        _safe_format pass (which only fills {problem}) would leave them as
        literal placeholders in the prompt to the model."""
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification()

        gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        _, gen_kwargs = gen.call_args
        user_template = gen_kwargs["user_template"]
        # Atom placeholders are pre-rendered with task values
        assert "L" in user_template  # left_anchor was "L"
        assert "R" in user_template  # right_anchor was "R"
        assert "induction" in user_template
        # Downstream placeholder is preserved for generate() to substitute
        assert "{problem}" in user_template

    def test_balanced_false_for_atom_generator(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification()

        gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        _, gen_kwargs = gen.call_args
        assert gen_kwargs["balanced"] is False

    def test_ledger_threaded_through(self, task, mocked_subagents):
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        ver.return_value = _make_verification()
        sentinel_ledger = MagicMock(name="ledger")

        gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
            ledger=sentinel_ledger,
        )

        # Both generate and verify must see the ledger
        _, gen_kwargs = gen.call_args
        assert gen_kwargs["ledger"] is sentinel_ledger
        _, ver_kwargs = ver.call_args
        assert ver_kwargs["ledger"] is sentinel_ledger

    def test_critical_issue_blocks_acceptance(self, task, mocked_subagents):
        """is_acceptable returns False when any issue has CRITICAL severity,
        even if verdict=CORRECT and confidence is high. Microkernel respects
        this — should fall through to revision."""
        gen, ver, rev = mocked_subagents
        gen.return_value = _make_solution("ATOM[GAP]\nbody")
        critical = Issue(text="serious flaw", severity=IssueSeverity.CRITICAL)
        # Initial: CORRECT but with a CRITICAL issue → not acceptable
        # Then revision succeeds cleanly
        ver.side_effect = [
            _make_verification(
                verdict=Verdict.CORRECT, confidence=0.95,
                critique="looks ok overall", issues=[critical],
            ),
            _make_verification(verdict=Verdict.CORRECT, confidence=0.95),
        ]
        rev.return_value = _make_solution("ATOM[GAP]\nfixed")

        result = gvr_microkernel(
            task, config=_make_config(), domain="math", client=MagicMock(),
        )

        assert result.status == "filled"
        assert result.revisions_used == 1
