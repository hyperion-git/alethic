"""
tests/test_probe_parsing.py -- Agent D: parsing and regex robustness probe tests.

Probe D1 -- Adversarial verifier output (duplicate fields, trailing text, nested markers)
Probe D2 -- Layer sentinel injection in parse_layer_results (no context awareness)
Probe D3 -- _safe_format cascade replacement when a value contains another key's placeholder
Probe D4 -- Pipe characters in ISSUES block (math notation like |x| > 0)
Probe D5 -- Unicode fullwidth digits in CONFIDENCE field
Probe D6 -- SequenceMatcher dedup threshold boundary at 0.6

Tests labelled "FAILS WITH CURRENT CODE" expose actual bugs.
Tests labelled "PASSES WITH CURRENT CODE" document correct/safe behavior.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import pytest

from alethic.autopsy import _best_per_iteration, _classify_failure_pattern
from alethic.models import (
    AgentEvent,
    AgentResult,
    EventType,
    Issue,
    IssueSeverity,
    Verdict,
    VerificationResult,
)
from alethic.physics_checks import parse_layer_results
from alethic.subagents import _parse_verification, _safe_format
from alethic.synthesizer import _similar, aggregate_mechanical


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verify_result(verdicts, confidences):
    """Build a minimal AgentResult with VERIFY events for autopsy tests."""
    events = []
    for i, (v, c) in enumerate(zip(verdicts, confidences, strict=True)):
        events.append(
            AgentEvent(
                type=EventType.VERIFY,
                iteration=i + 1,
                data={"verdict": v, "confidence": c},
            )
        )
    return AgentResult(
        problem="test",
        solution=None,
        verdict=Verdict.UNSOLVED,
        confidence=max(confidences) if confidences else 0.0,
        iterations_used=len(verdicts),
        total_revisions=0,
        admitted_failure=True,
        events=events,
    )


# ===========================================================================
# Probe D1 -- Adversarial verifier output
# ===========================================================================


class TestProbeD1AdversarialVerifierOutput:
    """Tests for robustness of _parse_verification against adversarial inputs."""

    def test_probe_d1_duplicate_verdict_first_wins(self):
        """PASSES WITH CURRENT CODE.

        Probe D1: re.search returns the FIRST VERDICT match. A second VERDICT:
        field later in the text is completely ignored. Documents that the parser
        is not greedily scanning to the last match.
        """
        text = (
            "VERDICT: CORRECT\n"
            "CONFIDENCE: 0.95\n"
            "\n"
            "CRITIQUE:\n"
            "Good.\n"
            "\n"
            "VERDICT: MAJOR_FLAW\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.verdict == Verdict.CORRECT, (
            f"First VERDICT must win; adversarial duplicate ignored. Got {result.verdict}"
        )

    def test_probe_d1_confidence_trailing_qualifier_ignored(self):
        """PASSES WITH CURRENT CODE.

        Probe D1: 'CONFIDENCE: 0.95 but actually 0.1' -- the [\\d.]+ pattern stops
        at the space after '0.95'. The qualifier text is ignored entirely.
        """
        text = (
            "VERDICT: correct\n"
            "CONFIDENCE: 0.95 but actually 0.1\n"
            "\n"
            "CRITIQUE:\n"
            "The solution is correct.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.confidence == 0.95, (
            f"Trailing qualifier text must not alter the parsed confidence value. "
            f"Got {result.confidence}"
        )

    def test_probe_d1_nested_end_corrected_solution_stops_at_first(self):
        """PASSES WITH CURRENT CODE.

        Probe D1: The CORRECTED SOLUTION regex uses non-greedy .*? and terminates
        on the FIRST 'END CORRECTED SOLUTION' line. Content injected after the
        first terminator does not leak into the captured block.
        """
        text = (
            "VERDICT: fixable\n"
            "CONFIDENCE: 0.70\n"
            "\n"
            "CRITIQUE:\n"
            "Sign error.\n"
            "\n"
            "ISSUES:\n"
            "- [MAJOR] Sign error\n"
            "\n"
            "CORRECTED SOLUTION:\n"
            "The corrected step: a + b = c\n"
            "END CORRECTED SOLUTION\n"
            "INJECTED CONTENT (adversary extends block)\n"
            "END CORRECTED SOLUTION\n"
        )
        result = _parse_verification(text)
        assert result.corrected_solution is not None
        assert "The corrected step: a + b = c" in result.corrected_solution
        assert "INJECTED CONTENT" not in result.corrected_solution, (
            "Content after first END CORRECTED SOLUTION must not appear in corrected_solution"
        )

    def test_probe_d1_unknown_verdict_defaults_to_major_flaw(self):
        """PASSES WITH CURRENT CODE.

        Probe D1: A VERDICT value not matching the allowed alternatives
        (correct|minor_issues|fixable|major_flaw|unsolved) causes regex failure;
        the parser defaults to MAJOR_FLAW. Novel strings cannot smuggle in a
        permissive verdict.
        """
        text = (
            "VERDICT: VERY_GOOD\n"
            "CONFIDENCE: 0.99\n"
            "\n"
            "CRITIQUE:\n"
            "Excellent!\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.verdict == Verdict.MAJOR_FLAW, (
            f"Unknown verdict string must default to MAJOR_FLAW, got {result.verdict}"
        )

    def test_probe_d1_corrected_solution_not_terminated_by_allcaps_label(self):
        """PASSES WITH CURRENT CODE.

        Probe D1: The CORRECTED SOLUTION block terminates ONLY on 'END CORRECTED
        SOLUTION', not on arbitrary ALL-CAPS labels like 'STEP ONE:' or 'NOTE:'.
        This was a documented historical fix in CLAUDE.md (subagents.py module note).
        """
        text = (
            "VERDICT: fixable\n"
            "CONFIDENCE: 0.72\n"
            "\n"
            "CRITIQUE:\n"
            "Minor error.\n"
            "\n"
            "ISSUES:\n"
            "- [MINOR] Small error\n"
            "\n"
            "CORRECTED SOLUTION:\n"
            "STEP ONE: Setup\n"
            "STEP TWO: Solve\n"
            "CONCLUSION: Answer is 42\n"
            "END CORRECTED SOLUTION\n"
        )
        result = _parse_verification(text)
        assert result.corrected_solution is not None
        assert "STEP ONE: Setup" in result.corrected_solution
        assert "STEP TWO: Solve" in result.corrected_solution
        assert "CONCLUSION: Answer is 42" in result.corrected_solution


# ===========================================================================
# Probe D2 -- Layer sentinel injection
# ===========================================================================


class TestProbeD2SentinelInjection:
    """Tests for parse_layer_results context-awareness (or lack thereof)."""

    def test_probe_d2_sentinel_in_python_comment_line_is_matched(self):
        """PASSES WITH CURRENT CODE -- re.match rejects non-start-of-line sentinels.

        Probe D2: parse_layer_results uses _SENTINEL_RE.match(line), so sentinels
        preceded by '#' (comments) are correctly rejected.
        """
        text = "# ALETHIC_L0_CHECK: PASS (injected by adversary)"
        result = parse_layer_results(text)
        assert result == {}, (
            f"parse_layer_results matched sentinel inside a Python comment line.\n"
            f"No context-awareness: comment sentinels must not be treated as results.\n"
            f"Got: {result}"
        )

    def test_probe_d2_sentinel_in_prose_explanation_is_matched(self):
        """PASSES WITH CURRENT CODE -- re.match rejects mid-line sentinel mentions.

        Probe D2: Prose mentioning sentinels mid-line is correctly rejected.
        """
        text = "Note: ALETHIC_L0_CHECK: may fail under adversarial conditions"
        result = parse_layer_results(text)
        assert result == {}, (
            f"parse_layer_results matched sentinel in a prose explanation.\n"
            f"Got: {result}"
        )

    def test_probe_d2_sentinel_with_arbitrary_prefix_is_matched(self):
        """PASSES WITH CURRENT CODE -- re.match rejects prefixed sentinel lines.

        Probe D2: _SENTINEL_RE uses re.match (not re.search), so the sentinel
        must appear at line start. Arbitrary prefixes are correctly rejected.
        """
        text = "DO NOT TRUST: ALETHIC_L0_CHECK: FAKE"
        result = parse_layer_results(text)
        assert result == {}, (
            f"Sentinel with arbitrary prefix text should not produce a result.\n"
            f"re.search matches anywhere on the line -- not just at line start.\n"
            f"Got: {result}"
        )

    def test_probe_d2_genuine_standalone_sentinel_is_correctly_detected(self):
        """PASSES WITH CURRENT CODE -- documents correct behavior.

        Probe D2: A genuine sentinel appearing on its own line (as it would be
        printed by actual verification code via print()) IS correctly detected.
        """
        text = (
            "Running verification...\n"
            "ALETHIC_L0_CHECK: DIMENSIONS OK\n"
            "Verification complete.\n"
        )
        result = parse_layer_results(text)
        assert result == {0: ["DIMENSIONS OK"]}, (
            f"Genuine standalone sentinel must be detected. Got {result}"
        )

    def test_probe_d2_multiple_layers_in_one_text_all_detected(self):
        """PASSES WITH CURRENT CODE -- documents multi-layer extraction.

        Probe D2: Multiple sentinel lines at different layers are all extracted
        correctly into separate dict entries.
        """
        text = (
            "ALETHIC_L0_CHECK: STRUCTURE OK\n"
            "ALETHIC_L1_CHECK: BASE CASES OK (n=0..4)\n"
            "ALETHIC_L2_CHECK: CONSISTENCY OK at n=10\n"
        )
        result = parse_layer_results(text)
        assert result == {
            0: ["STRUCTURE OK"],
            1: ["BASE CASES OK (n=0..4)"],
            2: ["CONSISTENCY OK at n=10"],
        }, f"All three layer sentinels must be detected. Got {result}"


# ===========================================================================
# Probe D3 -- _safe_format cascade replacement
# ===========================================================================


class TestProbeD3SafeFormatCascade:
    """Tests for _safe_format's sequential replacement cascade vulnerability."""

    def test_probe_d3_cascade_problem_value_containing_solution_placeholder(self):
        """PASSES WITH CURRENT CODE -- single-pass regex prevents cascade corruption.

        Probe D3: _safe_format uses re.sub with a callback for single-pass replacement.
        Placeholder-like text in replacement values (e.g., '{solution}' in problem text)
        is never re-processed, so it survives unchanged.
        """
        template = "PROBLEM:\n{problem}\n\nSOLUTION:\n{solution}"
        problem_text = "Find all {solution} to f(x) = 0"
        solution_text = "x = 1"

        result = _safe_format(template, problem=problem_text, solution=solution_text)

        expected = "PROBLEM:\nFind all {solution} to f(x) = 0\n\nSOLUTION:\nx = 1"
        # Actual: "PROBLEM:\nFind all x = 1 to f(x) = 0\n\nSOLUTION:\nx = 1"
        assert result == expected, (
            f"Cascade bug: _safe_format corrupted the problem text.\n"
            f"'{{solution}}' inside the problem value was replaced with solution text.\n"
            f"Got:      {result!r}\n"
            f"Expected: {expected!r}"
        )

    def test_probe_d3_cascade_reviser_problem_containing_critique_placeholder(self):
        """PASSES WITH CURRENT CODE -- single-pass regex prevents cascade corruption.

        Probe D3: Mirrors the actual revise() call pattern. '{critique}' in problem
        text survives intact because re.sub processes each match independently.
        """
        template = (
            "PROBLEM:\n{problem}\n\n"
            "SOLUTION:\n{solution}\n\n"
            "CRITIQUE:\n{critique}\n\n"
            "ISSUES:\n{issues}"
        )
        problem_text = "Analyze the {critique} of the proposed algorithm"
        solution_text = "Algorithm is O(n log n)"
        critique_text = "Missing edge case for empty input"
        issues_text = "- edge case missing"

        result = _safe_format(
            template,
            problem=problem_text,
            solution=solution_text,
            critique=critique_text,
            issues=issues_text,
        )

        # Extract what ended up in the PROBLEM section
        problem_start = result.find("PROBLEM:\n") + len("PROBLEM:\n")
        problem_end = result.find("\n\nSOLUTION:")
        extracted_problem = result[problem_start:problem_end]

        assert extracted_problem == problem_text, (
            f"Cascade bug: '{{critique}}' in problem text was replaced.\n"
            f"Got:      {extracted_problem!r}\n"
            f"Expected: {problem_text!r}"
        )

    def test_probe_d3_no_cascade_for_set_notation_curly_braces(self):
        """PASSES WITH CURRENT CODE -- documents safe behavior for non-key braces.

        Probe D3: Curly braces that don't match any kwarg key survive unchanged.
        Set notation like '{x : x > 0}' is the primary use case that _safe_format
        was designed to handle (previously caused KeyError with str.format()).
        """
        template = "PROBLEM:\n{problem}"
        problem_text = "Show that {x : x > 0} is an open set"

        result = _safe_format(template, problem=problem_text)

        expected = "PROBLEM:\nShow that {x : x > 0} is an open set"
        assert result == expected

    def test_probe_d3_unmatched_single_brace_in_value_survives(self):
        """PASSES WITH CURRENT CODE -- unmatched braces don't raise errors.

        Probe D3: A single unmatched '{' in a value is left as-is. Unlike
        str.format(), str.replace() doesn't parse brace syntax, so unmatched
        braces cause no ValueError.
        """
        template = "PROBLEM:\n{problem}"
        problem_text = "Consider the interval [0, 1) -- this is {half-open"

        result = _safe_format(template, problem=problem_text)

        assert "{half-open" in result


# ===========================================================================
# Probe D4 -- Pipe characters in ISSUES block
# ===========================================================================


class TestProbeD4PipeCharsInIssues:
    """Tests for pipe character handling in ISSUES and SECTION CONFIDENCES parsing."""

    def test_probe_d4_pipe_chars_in_issue_text_preserved(self):
        """PASSES WITH CURRENT CODE -- documents correct handling.

        Probe D4: Pipe characters in issue text (e.g., |x| > 0 for absolute value)
        are treated as literal characters. The parser splits on newlines and strips
        leading dashes/severity tags -- pipe chars within a line are untouched.
        """
        text = (
            "VERDICT: major_flaw\n"
            "CONFIDENCE: 0.3\n"
            "\n"
            "CRITIQUE:\n"
            "Bound is wrong.\n"
            "\n"
            "ISSUES:\n"
            "- [MAJOR] The inequality |x| > 0 is incorrectly applied at step 3\n"
            "- [MINOR] Missing case for |f(x)| = |g(x)|\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 2
        assert "|x| > 0" in result.issues[0].text
        assert "|f(x)| = |g(x)|" in result.issues[1].text

    def test_probe_d4_multiple_pipes_in_single_issue(self):
        """PASSES WITH CURRENT CODE.

        Probe D4: Multiple pipe characters in a single issue (e.g., triangle
        inequality |a| + |b| >= |a + b|) are all preserved.
        """
        text = (
            "VERDICT: major_flaw\n"
            "CONFIDENCE: 0.4\n"
            "\n"
            "CRITIQUE:\n"
            "Triangle inequality misapplied.\n"
            "\n"
            "ISSUES:\n"
            "- [MAJOR] Failed to apply |a| + |b| >= |a + b| correctly\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 1
        assert "|a| + |b| >= |a + b|" in result.issues[0].text

    def test_probe_d4_pipe_in_section_confidence_note_is_handled(self):
        """PASSES WITH CURRENT CODE.

        Probe D4: Pipe characters in SECTION CONFIDENCES note fields (e.g.,
        'steps 1|2 combined') do not break the section confidence parser.
        """
        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.80\n"
            "\n"
            "CRITIQUE:\n"
            "Mostly correct.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
            "\n"
            "SECTION CONFIDENCES:\n"
            "- setup: 0.90\n"
            "- derivation: 0.70 (steps 1|2 have gap)\n"
        )
        result = _parse_verification(text)
        assert len(result.section_confidences) == 2
        derivation = next(
            (sc for sc in result.section_confidences if "derivation" in sc.section),
            None,
        )
        assert derivation is not None
        assert derivation.confidence == 0.70


# ===========================================================================
# Probe D5 -- Unicode fullwidth digits in CONFIDENCE
# ===========================================================================


class TestProbeD5UnicodeFullwidthDigits:
    """Tests for safe handling of Unicode fullwidth digit characters in CONFIDENCE."""

    def test_probe_d5_fullwidth_confidence_parses_correctly(self):
        """PASSES WITH CURRENT CODE -- Python 3.13 float() accepts fullwidth digits.

        Probe D5: Python's re module matches fullwidth digits (U+FF10-FF19) with
        \\d since they are Unicode decimal digits (category Nd). As of Python 3.13,
        float() also accepts fullwidth digit strings, so the confidence parses
        correctly without hitting the fallback path.
        """
        text = (
            "VERDICT: correct\n"
            "CONFIDENCE: \uff10.\uff19\uff15\n"  # fullwidth: 0.95
            "\n"
            "CRITIQUE:\n"
            "The proof is correct.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.confidence == 0.95, (
            f"Python 3.13 float() handles fullwidth digits. Got {result.confidence}"
        )
        assert result.verdict == Verdict.CORRECT

    def test_probe_d5_standard_ascii_confidence_unaffected(self):
        """PASSES WITH CURRENT CODE -- baseline regression test for D5.

        Probe D5: Standard ASCII digits parse correctly. Ensures the fullwidth
        test doesn't reflect a general parsing regression.
        """
        text = (
            "VERDICT: correct\n"
            "CONFIDENCE: 0.95\n"
            "\n"
            "CRITIQUE:\n"
            "Correct.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.confidence == 0.95

    def test_probe_d5_regex_matches_fullwidth_and_float_accepts(self):
        """PASSES WITH CURRENT CODE -- Python 3.13 handles fullwidth digits end-to-end.

        Probe D5: Isolates the two-step mechanism:
        1. re.search with \\d DOES match fullwidth digits (Unicode Nd category).
        2. Python 3.13's float() also accepts fullwidth digit strings.
        Both fullwidth and ASCII confidence values parse identically.
        """
        pattern = re.compile(r"CONFIDENCE:\s*([\d.]+)", re.IGNORECASE)
        text_fullwidth = "CONFIDENCE: \uff10.\uff19\uff15"  # 0.95 in fullwidth
        text_ascii = "CONFIDENCE: 0.95"

        match_fullwidth = pattern.search(text_fullwidth)
        match_ascii = pattern.search(text_ascii)

        # Both patterns find a match -- fullwidth \d is a Unicode decimal digit
        assert match_fullwidth is not None, (
            "Python re \\d must match fullwidth Unicode digits (they are Unicode Nd category)"
        )
        assert match_ascii is not None

        # Both convert cleanly in Python 3.13+
        assert float(match_ascii.group(1)) == 0.95
        assert float(match_fullwidth.group(1)) == 0.95


# ===========================================================================
# Probe D6 -- SequenceMatcher dedup threshold at 0.6
# ===========================================================================


class TestProbeD6SequenceMatcherThreshold:
    """Tests for _similar() and aggregate_mechanical() deduplication boundary."""

    def test_probe_d6_similar_at_exact_threshold_returns_true(self):
        """PASSES WITH CURRENT CODE -- documents boundary behavior.

        Probe D6: Two strings with SequenceMatcher ratio exactly 0.6 should merge
        (threshold is >=). 'abcde' vs 'abcxy': matching block 'abc' (3 chars),
        total 10 chars. ratio = 2*3/10 = 0.6 exactly.
        """
        a, b = "abcde", "abcxy"
        ratio = SequenceMatcher(None, a, b).ratio()
        assert abs(ratio - 0.6) < 1e-9, (
            f"Test setup: expected ratio=0.6, got {ratio:.10f}"
        )
        assert _similar(a, b) is True, (
            f"At ratio={ratio:.4f} (exactly at 0.6 threshold), _similar must return True"
        )

    def test_probe_d6_similar_below_threshold_returns_false(self):
        """PASSES WITH CURRENT CODE -- documents boundary behavior.

        Probe D6: Two strings with ratio clearly below 0.6 must NOT merge.
        'abcde' vs 'abwxy': matching block 'ab' (2 chars), total 10 chars.
        ratio = 2*2/10 = 0.4. Below 0.6 -> not similar.
        """
        a, b = "abcde", "abwxy"
        ratio = SequenceMatcher(None, a, b).ratio()
        assert ratio < 0.6, f"Test setup: expected ratio<0.6, got {ratio:.4f}"
        assert _similar(a, b) is False, (
            f"At ratio={ratio:.4f} (below 0.6 threshold), _similar must return False"
        )

    def test_probe_d6_nearly_identical_issues_merge_in_aggregate(self):
        """PASSES WITH CURRENT CODE -- documents issue deduplication.

        Probe D6: Two near-identical issue texts (differing only in step number)
        have ratio well above 0.6 and must merge into a single entry with
        flagged_by=2.
        """
        issue_a = "Sign error in step 3 of the derivation"
        issue_b = "Sign error in step 4 of the derivation"
        ratio = SequenceMatcher(None, issue_a.lower(), issue_b.lower()).ratio()
        assert ratio >= 0.6, f"Test setup: ratio={ratio:.4f} should be >= 0.6"

        results = [
            VerificationResult(
                verdict=Verdict.MAJOR_FLAW,
                critique="sign error",
                confidence=0.30,
                issues=[Issue(text=issue_a, severity=IssueSeverity.MAJOR)],
            ),
            VerificationResult(
                verdict=Verdict.MAJOR_FLAW,
                critique="sign error again",
                confidence=0.35,
                issues=[Issue(text=issue_b, severity=IssueSeverity.MAJOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 1, (
            f"Similar issues (ratio={ratio:.4f}) must merge. Got {len(agg['issues'])} issues."
        )
        assert agg["issues"][0].flagged_by == 2

    def test_probe_d6_distinct_issues_stay_separate_in_aggregate(self):
        """PASSES WITH CURRENT CODE -- documents dedup does not over-merge.

        Probe D6: Clearly different issue texts remain as separate entries.
        """
        issue_a = "Missing normalization constant in the wave function"
        issue_b = "Boundary condition not verified at the origin"
        ratio = SequenceMatcher(None, issue_a.lower(), issue_b.lower()).ratio()
        assert ratio < 0.6, f"Test setup: ratio={ratio:.4f} should be < 0.6"

        results = [
            VerificationResult(
                verdict=Verdict.MAJOR_FLAW,
                critique="normalization issue",
                confidence=0.40,
                issues=[Issue(text=issue_a, severity=IssueSeverity.MAJOR)],
            ),
            VerificationResult(
                verdict=Verdict.MAJOR_FLAW,
                critique="boundary issue",
                confidence=0.45,
                issues=[Issue(text=issue_b, severity=IssueSeverity.MAJOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 2, (
            f"Distinct issues (ratio={ratio:.4f}) must remain separate. "
            f"Got {len(agg['issues'])} issues."
        )
        assert all(i.flagged_by == 1 for i in agg["issues"])

    def test_probe_d6_severity_escalates_when_similar_issues_merge(self):
        """PASSES WITH CURRENT CODE -- documents severity escalation on merge.

        Probe D6: When two similar issues merge, the most severe rating wins.
        MINOR + MAJOR -> MAJOR after merge.
        """
        issue_text = "Sign error in step 3"
        issue_variant = "Sign error in step 3 of the derivation"
        ratio = SequenceMatcher(
            None, issue_text.lower(), issue_variant.lower()
        ).ratio()
        assert ratio >= 0.6, f"Test setup: ratio={ratio:.4f} should be >= 0.6"

        results = [
            VerificationResult(
                verdict=Verdict.MINOR_ISSUES,
                critique="minor sign",
                confidence=0.80,
                issues=[Issue(text=issue_text, severity=IssueSeverity.MINOR)],
            ),
            VerificationResult(
                verdict=Verdict.MAJOR_FLAW,
                critique="major sign",
                confidence=0.50,
                issues=[Issue(text=issue_variant, severity=IssueSeverity.MAJOR)],
            ),
        ]
        agg = aggregate_mechanical(results)
        assert len(agg["issues"]) == 1
        assert agg["issues"][0].severity == IssueSeverity.MAJOR, (
            "Most severe rating must win when similar issues merge"
        )


# ===========================================================================
# Bonus: Autopsy classifier edge cases
# ===========================================================================


class TestBonusAutopsyClassifierEdgeCases:
    """Documents parsing-adjacent edge cases in the autopsy failure classifier."""

    def test_bonus_autopsy_three_alternating_verdicts_classified_as_regression(self):
        """PASSES WITH CURRENT CODE -- documents classification boundary.

        The oscillation check requires len(verdicts) >= 4. With only 3 events,
        even perfect alternation (major_flaw -> correct -> major_flaw) with dropping
        confidence falls through to regression (peak at idx=1, drops > 0.15).

        Documents: oscillation is NOT detected for sessions with < 4 iterations.
        """
        result = _make_verify_result(
            ["major_flaw", "correct", "major_flaw"],
            [0.2, 0.9, 0.2],
        )
        pattern = _classify_failure_pattern(result)
        assert pattern == "regression", (
            f"3-event alternation -> regression (oscillation requires >= 4 events). "
            f"Got {pattern}"
        )

    def test_bonus_autopsy_best_per_iteration_selects_highest_confidence(self):
        """PASSES WITH CURRENT CODE -- documents best-of-N event filtering.

        _best_per_iteration detects best-of-N mode when: (a) at least one event
        has a 'candidate' key, AND (b) there are multiple events per iteration.
        Returns only the highest-confidence event per iteration.
        """
        events = [
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"verdict": "minor_issues", "confidence": 0.7, "candidate": 0},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"verdict": "correct", "confidence": 0.9, "candidate": 1},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=2,
                data={"verdict": "correct", "confidence": 0.85, "candidate": 0},
            ),
        ]
        best = _best_per_iteration(events)
        assert len(best) == 2, f"Expected 2 events (one per iteration), got {len(best)}"
        iter1 = next(e for e in best if e.iteration == 1)
        assert iter1.data["confidence"] == 0.9, (
            "Must select the highest-confidence candidate per iteration"
        )

    def test_bonus_autopsy_best_per_iteration_passthrough_in_single_candidate_mode(self):
        """PASSES WITH CURRENT CODE -- documents N=1 passthrough behavior.

        Without any 'candidate' key in event data, _best_per_iteration returns
        the original event list unchanged (no best-of-N filtering applied).
        """
        events = [
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"verdict": "major_flaw", "confidence": 0.3},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=2,
                data={"verdict": "minor_issues", "confidence": 0.7},
            ),
        ]
        best = _best_per_iteration(events)
        assert len(best) == 2
        assert best[0].data["confidence"] == 0.3
        assert best[1].data["confidence"] == 0.7
