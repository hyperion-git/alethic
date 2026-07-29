"""Tests for src/alethic/explorer.py (v3.8 Alien-style technique enumeration)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alethic import explorer as ex
from alethic.explorer import (
    EXPLORER_SYSTEM_MATH,
    EXPLORER_SYSTEM_PHYSICS,
    EXPLORER_USER,
    Technique,
    _filter_novel,
    _format_tried,
    _parse_techniques,
    _select_system_prompt,
    enumerate_techniques,
)
from alethic.models import AgentConfig

# ──────────────────────────────────────────────────────────────────────────
# Technique dataclass
# ──────────────────────────────────────────────────────────────────────────


class TestTechnique:
    def test_construction(self) -> None:
        t = Technique(name="induction on n", coherence=0.7)
        assert t.name == "induction on n"
        assert t.coherence == 0.7

    def test_is_frozen(self) -> None:
        t = Technique(name="x", coherence=0.5)
        with pytest.raises(AttributeError):
            t.name = "y"  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        assert Technique(name="x", coherence=0.5) == Technique(name="x", coherence=0.5)

    def test_inequality(self) -> None:
        assert Technique(name="x", coherence=0.5) != Technique(name="x", coherence=0.6)


# ──────────────────────────────────────────────────────────────────────────
# _format_tried
# ──────────────────────────────────────────────────────────────────────────


class TestFormatTried:
    def test_empty(self) -> None:
        # Empty list must produce a non-empty, model-legible "none" marker.
        out = _format_tried([])
        assert out.strip()  # non-empty
        assert "none" in out.lower()

    def test_single_item(self) -> None:
        out = _format_tried(["induction on n"])
        assert "induction on n" in out

    def test_multiple_items(self) -> None:
        out = _format_tried(["induction", "contradiction", "AM-GM"])
        assert "induction" in out
        assert "contradiction" in out
        assert "AM-GM" in out

    def test_preserves_order_in_output(self) -> None:
        out = _format_tried(["first", "second", "third"])
        assert out.index("first") < out.index("second") < out.index("third")


# ──────────────────────────────────────────────────────────────────────────
# _parse_techniques
# ──────────────────────────────────────────────────────────────────────────


class TestParseTechniques:
    def test_basic_pairs(self) -> None:
        text = (
            "TECHNIQUE 1: induction on n\n"
            "COHERENCE 1: 0.8\n"
            "TECHNIQUE 2: contradiction\n"
            "COHERENCE 2: 0.5\n"
        )
        techs = _parse_techniques(text)
        assert techs == [
            Technique(name="induction on n", coherence=0.8),
            Technique(name="contradiction", coherence=0.5),
        ]

    def test_ignores_interleaved_prose(self) -> None:
        text = (
            "Here are some possibilities I considered.\n"
            "TECHNIQUE 1: AM-GM inequality\n"
            "(this one is particularly natural for sums)\n"
            "COHERENCE 1: 0.7\n"
            "I think there is also a Cauchy-Schwarz angle:\n"
            "TECHNIQUE 2: Cauchy-Schwarz\n"
            "COHERENCE 2: 0.4\n"
        )
        techs = _parse_techniques(text)
        assert [t.name for t in techs] == ["AM-GM inequality", "Cauchy-Schwarz"]
        assert [t.coherence for t in techs] == [0.7, 0.4]

    def test_empty_text_returns_empty(self) -> None:
        assert _parse_techniques("") == []

    def test_no_techniques_returns_empty(self) -> None:
        assert _parse_techniques("no enumerated suggestions here\nmaybe try harder.") == []

    def test_coherence_clamped_above(self) -> None:
        text = "TECHNIQUE 1: foo\nCOHERENCE 1: 1.5\n"
        assert _parse_techniques(text) == [Technique(name="foo", coherence=1.0)]

    def test_coherence_clamped_below(self) -> None:
        text = "TECHNIQUE 1: foo\nCOHERENCE 1: -0.3\n"
        assert _parse_techniques(text) == [Technique(name="foo", coherence=0.0)]

    def test_missing_coherence_defaults_to_half(self) -> None:
        # Generator gave us a technique name but forgot the coherence line.
        # Don't silently drop it — assume a neutral 0.5 prior so the search
        # layer can still try the technique. (Common LLM omission to defend.)
        text = "TECHNIQUE 1: integration by parts\nTECHNIQUE 2: substitution u=x^2\nCOHERENCE 2: 0.7\n"
        techs = _parse_techniques(text)
        assert techs == [
            Technique(name="integration by parts", coherence=0.5),
            Technique(name="substitution u=x^2", coherence=0.7),
        ]

    def test_coherence_without_technique_ignored(self) -> None:
        # Stray COHERENCE 7 with no matching TECHNIQUE 7 → ignore (no orphan tech).
        text = "TECHNIQUE 1: foo\nCOHERENCE 1: 0.5\nCOHERENCE 7: 0.9\n"
        assert _parse_techniques(text) == [Technique(name="foo", coherence=0.5)]

    def test_non_numeric_coherence_defaults_to_half(self) -> None:
        text = "TECHNIQUE 1: foo\nCOHERENCE 1: high\n"
        assert _parse_techniques(text) == [Technique(name="foo", coherence=0.5)]

    def test_case_insensitive_labels(self) -> None:
        # Non-Claude models may produce lowercase or mixed-case labels.
        text = "technique 1: foo\ncoherence 1: 0.6\nTechnique 2: bar\nCoherence 2: 0.3\n"
        techs = _parse_techniques(text)
        assert techs == [
            Technique(name="foo", coherence=0.6),
            Technique(name="bar", coherence=0.3),
        ]

    def test_strips_whitespace_from_name(self) -> None:
        text = "TECHNIQUE 1:    induction on n   \nCOHERENCE 1: 0.8\n"
        assert _parse_techniques(text)[0].name == "induction on n"

    def test_strips_markdown_bold_labels(self) -> None:
        # Same robustness as verifier parser — non-Claude models add **bold**.
        text = "**TECHNIQUE 1:** induction\n**COHERENCE 1:** 0.7\n"
        techs = _parse_techniques(text)
        assert techs == [Technique(name="induction", coherence=0.7)]

    def test_blank_name_dropped(self) -> None:
        # `TECHNIQUE 1:` with empty body is junk — drop it.
        text = "TECHNIQUE 1: \nCOHERENCE 1: 0.5\nTECHNIQUE 2: real one\nCOHERENCE 2: 0.4\n"
        techs = _parse_techniques(text)
        assert techs == [Technique(name="real one", coherence=0.4)]


# ──────────────────────────────────────────────────────────────────────────
# _filter_novel
# ──────────────────────────────────────────────────────────────────────────


class TestFilterNovel:
    def test_empty_tried_keeps_all(self) -> None:
        techs = [Technique("a", 0.5), Technique("b", 0.4)]
        assert _filter_novel(techs, tried=[]) == techs

    def test_filters_exact_match(self) -> None:
        techs = [Technique("a", 0.5), Technique("b", 0.4)]
        assert _filter_novel(techs, tried=["a"]) == [Technique("b", 0.4)]

    def test_case_insensitive_filter(self) -> None:
        # 'induction' and 'Induction' refer to the same technique.
        techs = [Technique("Induction", 0.7), Technique("contradiction", 0.5)]
        assert _filter_novel(techs, tried=["induction"]) == [Technique("contradiction", 0.5)]

    def test_whitespace_insensitive_filter(self) -> None:
        # Surrounding whitespace shouldn't fool the dedup either.
        techs = [Technique("induction  ", 0.7), Technique("contradiction", 0.5)]
        assert _filter_novel(techs, tried=["  induction"]) == [Technique("contradiction", 0.5)]

    def test_preserves_input_order(self) -> None:
        techs = [Technique("x", 0.1), Technique("y", 0.9), Technique("z", 0.5)]
        assert _filter_novel(techs, tried=["y"]) == [Technique("x", 0.1), Technique("z", 0.5)]

    def test_all_filtered_returns_empty(self) -> None:
        techs = [Technique("a", 0.5), Technique("b", 0.4)]
        assert _filter_novel(techs, tried=["A", "B"]) == []

    def test_dedups_intra_call_duplicates(self) -> None:
        # If the LLM proposes the same technique twice in one call, keep first.
        techs = [Technique("induction", 0.7), Technique("Induction", 0.4), Technique("other", 0.5)]
        assert _filter_novel(techs, tried=[]) == [
            Technique("induction", 0.7),
            Technique("other", 0.5),
        ]


# ──────────────────────────────────────────────────────────────────────────
# _select_system_prompt
# ──────────────────────────────────────────────────────────────────────────


class TestSelectSystemPrompt:
    def test_math(self) -> None:
        assert _select_system_prompt("math") == EXPLORER_SYSTEM_MATH

    def test_physics(self) -> None:
        assert _select_system_prompt("physics") == EXPLORER_SYSTEM_PHYSICS

    def test_unknown_defaults_to_math(self) -> None:
        # Match microkernel convention: math is canonical, physics is override.
        assert _select_system_prompt("biology") == EXPLORER_SYSTEM_MATH


# ──────────────────────────────────────────────────────────────────────────
# Prompt template sanity
# ──────────────────────────────────────────────────────────────────────────


class TestPromptTemplates:
    def test_user_has_all_placeholders(self) -> None:
        for p in ("{left_anchor}", "{right_anchor}", "{tried_techniques}", "{problem}"):
            assert p in EXPLORER_USER, f"missing {p} in EXPLORER_USER"

    def test_user_describes_output_format(self) -> None:
        # The prompt must instruct the model to use the TECHNIQUE/COHERENCE
        # paired format the parser expects. Otherwise the parser is parsing
        # output the prompt never asked for.
        assert "TECHNIQUE" in EXPLORER_USER
        assert "COHERENCE" in EXPLORER_USER

    def test_user_mentions_coherence_range(self) -> None:
        # Make the 0.0-1.0 scale explicit so models don't return 0-100 etc.
        assert "0.0" in EXPLORER_USER and "1.0" in EXPLORER_USER

    def test_math_system_signals_role(self) -> None:
        # Sanity: math system prompt mentions mathematics / proof framing.
        assert any(w in EXPLORER_SYSTEM_MATH.lower() for w in ("math", "proof"))

    def test_physics_system_signals_role(self) -> None:
        assert any(w in EXPLORER_SYSTEM_PHYSICS.lower() for w in ("physics", "derivation"))


# ──────────────────────────────────────────────────────────────────────────
# enumerate_techniques — end-to-end with mocked _call_model
# ──────────────────────────────────────────────────────────────────────────


def _make_config() -> AgentConfig:
    return AgentConfig()


@pytest.fixture
def mock_call(monkeypatch):
    """Replace explorer._call_model with a MagicMock. Tests set return_value."""
    m = MagicMock()
    monkeypatch.setattr(ex, "_call_model", m)
    return m


class TestEnumerateTechniques:
    def test_returns_parsed_techniques(self, mock_call) -> None:
        mock_call.return_value = (
            "TECHNIQUE 1: induction on n\n"
            "COHERENCE 1: 0.8\n"
            "TECHNIQUE 2: contradiction\n"
            "COHERENCE 2: 0.5\n"
        )
        result = enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(),
        )
        assert result == [
            Technique(name="induction on n", coherence=0.8),
            Technique(name="contradiction", coherence=0.5),
        ]

    def test_filters_against_tried(self, mock_call) -> None:
        mock_call.return_value = (
            "TECHNIQUE 1: induction\n"
            "COHERENCE 1: 0.7\n"
            "TECHNIQUE 2: AM-GM\n"
            "COHERENCE 2: 0.4\n"
        )
        result = enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=["Induction"],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(),
        )
        assert result == [Technique(name="AM-GM", coherence=0.4)]

    def test_empty_when_all_already_tried(self, mock_call) -> None:
        mock_call.return_value = (
            "TECHNIQUE 1: induction\n"
            "COHERENCE 1: 0.7\n"
            "TECHNIQUE 2: contradiction\n"
            "COHERENCE 2: 0.5\n"
        )
        result = enumerate_techniques(
            left_anchor="L", right_anchor="R",
            tried_techniques=["induction", "contradiction"],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(),
        )
        assert result == []

    def test_uses_physics_system_prompt_for_physics(self, mock_call) -> None:
        mock_call.return_value = "TECHNIQUE 1: dimensional analysis\nCOHERENCE 1: 0.6\n"
        enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=_make_config(), domain="physics",
            client=MagicMock(),
        )
        kwargs = mock_call.call_args.kwargs
        assert kwargs["system"] == EXPLORER_SYSTEM_PHYSICS

    def test_uses_math_system_prompt_for_math(self, mock_call) -> None:
        mock_call.return_value = "TECHNIQUE 1: induction\nCOHERENCE 1: 0.6\n"
        enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(),
        )
        kwargs = mock_call.call_args.kwargs
        assert kwargs["system"] == EXPLORER_SYSTEM_MATH

    def test_user_message_contains_anchors_and_problem(self, mock_call) -> None:
        mock_call.return_value = ""
        enumerate_techniques(
            left_anchor="LEFT_ANCHOR_CONTENT",
            right_anchor="RIGHT_ANCHOR_CONTENT",
            tried_techniques=["bisection"],
            problem_context="THE_PROBLEM_STATEMENT",
            config=_make_config(), domain="math", client=MagicMock(),
        )
        user_msg = mock_call.call_args.kwargs["user_message"]
        assert "LEFT_ANCHOR_CONTENT" in user_msg
        assert "RIGHT_ANCHOR_CONTENT" in user_msg
        assert "THE_PROBLEM_STATEMENT" in user_msg
        assert "bisection" in user_msg

    def test_passes_ledger_through(self, mock_call) -> None:
        mock_call.return_value = ""
        ledger = MagicMock()
        enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(), ledger=ledger,
        )
        assert mock_call.call_args.kwargs["ledger"] is ledger

    def test_uses_generator_temperature(self, mock_call) -> None:
        # Exploration should sample diversely — use temperature_generator,
        # not temperature_verifier (which is intentionally low).
        mock_call.return_value = ""
        cfg = AgentConfig(temperature_generator=0.95, temperature_verifier=0.1)
        enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=cfg, domain="math", client=MagicMock(),
        )
        assert mock_call.call_args.kwargs["temperature"] == pytest.approx(0.95)

    def test_passes_config_through(self, mock_call) -> None:
        mock_call.return_value = ""
        cfg = _make_config()
        enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=cfg, domain="math", client=MagicMock(),
        )
        assert mock_call.call_args.kwargs["config"] is cfg

    def test_empty_response_returns_empty_list(self, mock_call) -> None:
        # Robustness: no enumerations from the model → empty, not exception.
        mock_call.return_value = "I'm not sure how to bridge this."
        result = enumerate_techniques(
            left_anchor="L", right_anchor="R", tried_techniques=[],
            problem_context="P", config=_make_config(), domain="math",
            client=MagicMock(),
        )
        assert result == []

    def test_anchor_with_braces_does_not_crash(self, mock_call) -> None:
        # LaTeX in anchors (e.g. \frac{a}{b}) breaks naive str.format.
        # _safe_format must handle this — verified by no exception raised.
        mock_call.return_value = "TECHNIQUE 1: x\nCOHERENCE 1: 0.5\n"
        enumerate_techniques(
            left_anchor=r"\frac{a}{b} = c",
            right_anchor=r"\sum_{i=0}^{n} x_i",
            tried_techniques=[],
            problem_context=r"Solve \int_{0}^{1} f(x)\,dx",
            config=_make_config(), domain="math", client=MagicMock(),
        )
        # If we got here, the prompt rendered without KeyError or IndexError.
        user_msg = mock_call.call_args.kwargs["user_message"]
        assert r"\frac{a}{b}" in user_msg
