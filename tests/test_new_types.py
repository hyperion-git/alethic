"""Tests for new types: Issue, SectionConfidence, AgentEvent, EventType, IssueSeverity.

Also tests updated VerificationResult (Issue objects, CRITICAL blocking),
AgentResult (events field, deprecated history, failed_approaches), and
the RunState/EventLog helpers, _summarize_failed_approach, and failed_approaches
wiring into generate().
"""

from __future__ import annotations

import time
import warnings
from unittest.mock import MagicMock

import pytest

from alethic.models import (
    AgentConfig,
    AgentEvent,
    AgentResult,
    EventType,
    Issue,
    IssueSeverity,
    SectionConfidence,
    Verdict,
    VerificationResult,
)

# ── IssueSeverity ───────────────────────────────────────────────────


class TestIssueSeverity:
    def test_enum_values(self):
        assert IssueSeverity.CRITICAL.value == "critical"
        assert IssueSeverity.MAJOR.value == "major"
        assert IssueSeverity.MINOR.value == "minor"


# ── Issue ───────────────────────────────────────────────────────────


class TestIssue:
    def test_construction(self):
        issue = Issue(text="Division by zero")
        assert issue.text == "Division by zero"
        assert issue.severity == IssueSeverity.MAJOR
        assert issue.addressed is False

    def test_defaults(self):
        issue = Issue(text="err")
        assert issue.severity == IssueSeverity.MAJOR
        assert issue.addressed is False

    def test_str(self):
        issue = Issue(text="Sign error in step 3")
        assert str(issue) == "Sign error in step 3"

    def test_fstring(self):
        issue = Issue(text="Missing bound")
        assert f"Issue: {issue}" == "Issue: Missing bound"

    def test_frozen(self):
        issue = Issue(text="err")
        with pytest.raises(AttributeError):
            issue.text = "new"  # type: ignore[misc]

    def test_equality(self):
        a = Issue(text="err", severity=IssueSeverity.MAJOR)
        b = Issue(text="err", severity=IssueSeverity.MAJOR)
        assert a == b

    def test_inequality_severity(self):
        a = Issue(text="err", severity=IssueSeverity.MAJOR)
        b = Issue(text="err", severity=IssueSeverity.MINOR)
        assert a != b

    def test_addressed_flag(self):
        issue = Issue(text="err", addressed=True)
        assert issue.addressed is True


# ── SectionConfidence ───────────────────────────────────────────────


class TestSectionConfidence:
    def test_construction(self):
        sc = SectionConfidence(section="Step 1", confidence=0.95)
        assert sc.section == "Step 1"
        assert sc.confidence == 0.95
        assert sc.note == ""

    def test_with_note(self):
        sc = SectionConfidence(section="Step 2", confidence=0.7, note="Shaky reasoning")
        assert sc.note == "Shaky reasoning"


# ── EventType ───────────────────────────────────────────────────────


class TestEventType:
    def test_all_values(self):
        assert EventType.GENERATE.value == "generate"
        assert EventType.VERIFY.value == "verify"
        assert EventType.REVISE.value == "revise"
        assert EventType.ERROR.value == "error"
        assert EventType.ACCEPT.value == "accept"
        assert EventType.FAIL.value == "fail"
        assert EventType.STALL_RESET.value == "stall_reset"

    def test_exactly_seven_members(self):
        assert len(EventType) == 7


# ── Stall Reset Config ─────────────────────────────────────────────


class TestStallResetConfig:
    def test_default_values(self):
        config = AgentConfig()
        assert config.stall_window == 2
        assert config.stall_epsilon == 0.03
        assert config.stall_reset is True
        assert config.reset_n_boost == 1

    def test_stall_reset_disabled(self):
        config = AgentConfig(stall_reset=False)
        assert config.stall_reset is False

    def test_validation_stall_window_positive(self):
        with pytest.raises(ValueError, match="stall_window must be >= 1"):
            AgentConfig(stall_window=0)

    def test_validation_stall_epsilon_nonneg(self):
        with pytest.raises(ValueError, match="stall_epsilon must be >= 0"):
            AgentConfig(stall_epsilon=-0.01)

    def test_validation_reset_n_boost_nonneg(self):
        with pytest.raises(ValueError, match="reset_n_boost must be >= 0"):
            AgentConfig(reset_n_boost=-1)


# ── AgentEvent ──────────────────────────────────────────────────────


class TestAgentEvent:
    def test_construction(self):
        event = AgentEvent(type=EventType.GENERATE, iteration=1)
        assert event.type == EventType.GENERATE
        assert event.iteration == 1

    def test_auto_timestamp(self):
        before = time.time()
        event = AgentEvent(type=EventType.VERIFY, iteration=2)
        after = time.time()
        assert before <= event.timestamp <= after

    def test_empty_data_default(self):
        event = AgentEvent(type=EventType.ERROR, iteration=3)
        assert event.data == {}

    def test_custom_data(self):
        event = AgentEvent(
            type=EventType.GENERATE,
            iteration=1,
            data={"candidate": 2, "solution_preview": "abc"},
        )
        assert event.data["candidate"] == 2


# ── VerificationResult with Issue type ──────────────────────────────


class TestVerificationResultIssueType:
    def test_issue_objects(self):
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Small error",
            confidence=0.8,
            issues=[Issue(text="Sign error"), Issue(text="Missing bound")],
        )
        assert len(vr.issues) == 2
        assert isinstance(vr.issues[0], Issue)
        assert vr.issues[0].text == "Sign error"

    def test_critical_blocks_acceptance(self):
        vr = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="Looks good but critical flaw",
            confidence=0.95,
            issues=[Issue(text="Overflow", severity=IssueSeverity.CRITICAL)],
        )
        assert not vr.is_acceptable()

    def test_non_critical_allows_acceptance(self):
        vr = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="Good",
            confidence=0.95,
            issues=[Issue(text="Minor notation", severity=IssueSeverity.MINOR)],
        )
        assert vr.is_acceptable()

    def test_section_confidences(self):
        vr = VerificationResult(
            verdict=Verdict.CORRECT,
            critique="Good",
            confidence=0.9,
            section_confidences=[
                SectionConfidence(section="Step 1", confidence=0.95),
                SectionConfidence(section="Step 2", confidence=0.85),
            ],
        )
        assert len(vr.section_confidences) == 2
        assert vr.section_confidences[0].section == "Step 1"

    def test_str_format(self):
        vr = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Almost",
            confidence=0.8,
            issues=[Issue(text="Fix step 2")],
        )
        text = str(vr)
        assert "Fix step 2" in text
        assert "Issues:" in text


# ── AgentResult with events ─────────────────────────────────────────


class TestAgentResultEvents:
    def test_events_field(self):
        events = [
            AgentEvent(type=EventType.GENERATE, iteration=1, data={"candidate": 1}),
            AgentEvent(type=EventType.VERIFY, iteration=1, data={"verdict": "correct"}),
        ]
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=events,
        )
        assert len(result.events) == 2
        assert result.events[0].type == EventType.GENERATE

    def test_history_backward_compat(self):
        events = [
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                data={"candidate": 1, "solution_preview": "abc"},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                data={"verdict": "correct", "confidence": 0.95},
            ),
        ]
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=events,
        )
        # Suppress the deprecation warning for this test
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            history = result.history

        assert len(history) == 2
        assert history[0]["phase"] == "generate"
        assert history[0]["candidate"] == 1
        assert history[1]["phase"] == "verify"
        assert history[1]["confidence"] == 0.95

    def test_history_warns(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=[],
        )
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _ = result.history
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()

    def test_failed_approaches(self):
        result = AgentResult(
            problem="test",
            solution=None,
            verdict=Verdict.UNSOLVED,
            confidence=0.3,
            iterations_used=5,
            total_revisions=10,
            admitted_failure=True,
            failed_approaches=["direct proof", "contradiction"],
        )
        assert len(result.failed_approaches) == 2
        assert "Failed approaches: 2" in str(result)

    def test_empty_failed_approaches_not_in_str(self):
        result = AgentResult(
            problem="test",
            solution="answer",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
        )
        assert "Failed approaches" not in str(result)


# ── RunState and EventLog ───────────────────────────────────────────


class TestRunStateAndEventLog:
    def test_run_state_defaults(self):
        from alethic.agent import RunState

        state = RunState()
        assert state.total_revisions == 0
        assert state.best_solution is None
        assert state.best_confidence == 0.0
        assert state.failed_approaches == []
        assert isinstance(state.start_time, float)

    def test_event_log_emit(self):
        from alethic.agent import EventLog

        log = EventLog()
        log.emit(EventType.GENERATE, iteration=1, candidate=1)
        log.emit(EventType.VERIFY, iteration=1, verdict="correct")

        assert len(log.events) == 2
        assert log.events[0].type == EventType.GENERATE
        assert log.events[0].iteration == 1
        assert log.events[0].data["candidate"] == 1
        assert log.events[1].data["verdict"] == "correct"


# ── _summarize_failed_approach ─────────────────────────────────────


class TestSummarizeFailedApproach:
    def test_extracts_first_sentence_and_top_issue(self):
        from alethic.agent import _summarize_failed_approach

        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="The induction step fails at the boundary. Also missing base case.",
            confidence=0.3,
            issues=[
                Issue(text="Induction hypothesis not applied correctly"),
                Issue(text="Base case missing"),
            ],
        )
        summary = _summarize_failed_approach(vr)
        assert len(summary) <= 200
        assert "induction" in summary.lower()
        assert "Issue:" in summary

    def test_handles_empty_issues(self):
        from alethic.agent import _summarize_failed_approach

        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique="Direct computation approach diverges. No convergence.",
            confidence=0.2,
            issues=[],
        )
        summary = _summarize_failed_approach(vr)
        assert "diverges" in summary.lower()
        assert "Issue:" not in summary

    def test_handles_long_critique(self):
        from alethic.agent import _summarize_failed_approach

        long_critique = "A" * 500 + ". Second sentence here."
        vr = VerificationResult(
            verdict=Verdict.MAJOR_FLAW,
            critique=long_critique,
            confidence=0.1,
            issues=[Issue(text="Everything wrong")],
        )
        summary = _summarize_failed_approach(vr)
        assert len(summary) <= 200


# ── Failed approach wiring into generate() ─────────────────────────


class TestFailedApproachInGenerate:
    def _make_mock_client(self):
        """Create a mock Anthropic client that returns a simple text response."""
        client = MagicMock()
        response = MagicMock()
        text_block = MagicMock()
        text_block.text = "Solution text"
        text_block.type = "text"
        response.content = [text_block]
        response.stop_reason = "end_turn"
        client.messages.create.return_value = response
        return client

    def test_generate_accepts_failed_approaches(self):
        from alethic.subagents import generate

        client = self._make_mock_client()
        config = AgentConfig(enable_code_execution=False, verbose=False)

        generate(
            client,
            problem="Prove sqrt(2) is irrational",
            config=config,
            iteration=1,
            balanced=False,
            failed_approaches=("Tried induction, base case fails",),
        )

        # Check the user message passed to messages.create
        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Previously attempted" in user_msg
        assert "Tried induction, base case fails" in user_msg

    def test_generate_no_failed_approaches(self):
        from alethic.subagents import generate

        client = self._make_mock_client()
        config = AgentConfig(enable_code_execution=False, verbose=False)

        generate(
            client,
            problem="Prove sqrt(2) is irrational",
            config=config,
            iteration=1,
            balanced=False,
        )

        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Previously attempted" not in user_msg


# ── Verifier prompt severity tags (Task 3.1) ─────────────────────


class TestVerifierPromptSeverity:
    def test_math_verifier_requests_severity_tags(self):
        from alethic.prompts import VERIFIER_SYSTEM

        assert "[CRITICAL]" in VERIFIER_SYSTEM
        assert "[MAJOR]" in VERIFIER_SYSTEM
        assert "[MINOR]" in VERIFIER_SYSTEM

    def test_physics_verifier_requests_severity_tags(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM

        assert "[CRITICAL]" in PHYSICS_VERIFIER_SYSTEM
        assert "[MAJOR]" in PHYSICS_VERIFIER_SYSTEM
        assert "[MINOR]" in PHYSICS_VERIFIER_SYSTEM

    def test_math_verifier_requests_section_confidences(self):
        from alethic.prompts import VERIFIER_SYSTEM

        assert "SECTION CONFIDENCES:" in VERIFIER_SYSTEM

    def test_physics_verifier_requests_section_confidences(self):
        from alethic.physics_prompts import PHYSICS_VERIFIER_SYSTEM

        assert "SECTION CONFIDENCES:" in PHYSICS_VERIFIER_SYSTEM


# ── Severity parsing (Task 3.2) ──────────────────────────────────


class TestSeverityParsing:
    def test_tagged_issues(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.75\n"
            "\n"
            "CRITIQUE:\n"
            "Step 2 has a sign error.\n"
            "\n"
            "REASON: N/A\n"
            "\n"
            "ISSUES:\n"
            "- [CRITICAL] Division by zero in step 3\n"
            "- [MINOR] Notation inconsistency\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 2
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[0].text == "Division by zero in step 3"
        assert result.issues[1].severity == IssueSeverity.MINOR
        assert result.issues[1].text == "Notation inconsistency"

    def test_untagged_issues_default_major(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.70\n"
            "\n"
            "CRITIQUE:\n"
            "Some issues.\n"
            "\n"
            "ISSUES:\n"
            "- Sign error in step 2\n"
            "- Missing bound check\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 2
        assert result.issues[0].severity == IssueSeverity.MAJOR
        assert result.issues[1].severity == IssueSeverity.MAJOR

    def test_mixed_tagged_and_untagged(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: major_flaw\n"
            "CONFIDENCE: 0.40\n"
            "\n"
            "CRITIQUE:\n"
            "Multiple problems.\n"
            "\n"
            "ISSUES:\n"
            "- [CRITICAL] Circular reasoning\n"
            "- Missing justification for step 4\n"
            "- [MINOR] Typo in equation 3\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 3
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[1].severity == IssueSeverity.MAJOR  # untagged default
        assert result.issues[2].severity == IssueSeverity.MINOR

    def test_case_insensitive_tags(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.70\n"
            "\n"
            "CRITIQUE:\n"
            "Issues found.\n"
            "\n"
            "ISSUES:\n"
            "- [critical] Fundamental flaw\n"
            "- [Minor] Small typo\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 2
        assert result.issues[0].severity == IssueSeverity.CRITICAL
        assert result.issues[1].severity == IssueSeverity.MINOR

    def test_unknown_tag_defaults_major(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.70\n"
            "\n"
            "CRITIQUE:\n"
            "Issues found.\n"
            "\n"
            "ISSUES:\n"
            "- [WARNING] Some warning\n"
        )
        result = _parse_verification(text)
        assert len(result.issues) == 1
        assert result.issues[0].severity == IssueSeverity.MAJOR
        assert result.issues[0].text == "Some warning"

    def test_none_issues_still_empty(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: correct\n"
            "CONFIDENCE: 0.95\n"
            "\n"
            "CRITIQUE:\n"
            "Perfect solution.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.issues == []


# ── Section confidence parsing (Task 3.2) ────────────────────────


class TestSectionConfidenceParsing:
    def test_section_confidences_parsed(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.80\n"
            "\n"
            "CRITIQUE:\n"
            "Mostly good.\n"
            "\n"
            "ISSUES:\n"
            "- [MINOR] Small gap\n"
            "\n"
            "SECTION CONFIDENCES:\n"
            "- Setup: 0.95 Clear and correct\n"
            "- Main proof: 0.70\n"
            "- Conclusion: 0.85 Needs minor polish\n"
        )
        result = _parse_verification(text)
        assert len(result.section_confidences) == 3
        assert result.section_confidences[0].section == "Setup"
        assert result.section_confidences[0].confidence == 0.95
        assert result.section_confidences[0].note == "Clear and correct"
        assert result.section_confidences[1].section == "Main proof"
        assert result.section_confidences[1].confidence == 0.70
        assert result.section_confidences[1].note == ""
        assert result.section_confidences[2].section == "Conclusion"
        assert result.section_confidences[2].confidence == 0.85
        assert result.section_confidences[2].note == "Needs minor polish"

    def test_missing_section_confidences(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: correct\n"
            "CONFIDENCE: 0.95\n"
            "\n"
            "CRITIQUE:\n"
            "All good.\n"
            "\n"
            "ISSUES:\n"
            "None\n"
        )
        result = _parse_verification(text)
        assert result.section_confidences == []

    def test_malformed_section_confidence_skipped(self):
        from alethic.subagents import _parse_verification

        text = (
            "VERDICT: minor_issues\n"
            "CONFIDENCE: 0.80\n"
            "\n"
            "CRITIQUE:\n"
            "Some issues.\n"
            "\n"
            "ISSUES:\n"
            "- [MINOR] Typo\n"
            "\n"
            "SECTION CONFIDENCES:\n"
            "- Setup: 0.90 Looks good\n"
            "- This line has no colon or number\n"
            "- Conclusion: 0.85\n"
        )
        result = _parse_verification(text)
        assert len(result.section_confidences) == 2
        assert result.section_confidences[0].section == "Setup"
        assert result.section_confidences[1].section == "Conclusion"


# ── Reviser section targeting (Task 3.3) ─────────────────────────


# ── CLI event output (Task 4.1) ──────────────────────────────────


class TestCLIEventOutput:
    def test_json_output_includes_events(self):
        import json

        events = [
            AgentEvent(
                type=EventType.GENERATE,
                iteration=1,
                timestamp=1000.0,
                data={"candidate": 1, "solution_preview": "Let x = ..."},
            ),
            AgentEvent(
                type=EventType.VERIFY,
                iteration=1,
                timestamp=1001.0,
                data={"verdict": "correct", "confidence": 0.95},
            ),
            AgentEvent(
                type=EventType.ACCEPT,
                iteration=1,
                timestamp=1002.0,
                data={"final_confidence": 0.95},
            ),
        ]
        result = AgentResult(
            problem="Prove sqrt(2) is irrational",
            solution="Assume sqrt(2) = p/q ...",
            verdict=Verdict.CORRECT,
            confidence=0.95,
            iterations_used=1,
            total_revisions=0,
            admitted_failure=False,
            events=events,
            elapsed_seconds=12.5,
            candidates_per_iteration=2,
            failed_approaches=["Direct computation diverges", "Induction base case fails"],
        )

        # Build the output dict exactly as cli.py does
        output = {
            "problem": result.problem,
            "solved": result.solved,
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "iterations_used": result.iterations_used,
            "total_revisions": result.total_revisions,
            "candidates_per_iteration": result.candidates_per_iteration,
            "admitted_failure": result.admitted_failure,
            "elapsed_seconds": result.elapsed_seconds,
            "solution": result.solution,
            "failed_approaches": result.failed_approaches,
            "events": [
                {
                    "type": e.type.value,
                    "iteration": e.iteration,
                    "timestamp": e.timestamp,
                    **e.data,
                }
                for e in result.events
            ],
        }

        # Serialize to JSON and parse back
        json_str = json.dumps(output, indent=2)
        parsed = json.loads(json_str)

        # Verify top-level fields
        assert parsed["problem"] == "Prove sqrt(2) is irrational"
        assert parsed["solved"] is True
        assert parsed["verdict"] == "correct"
        assert parsed["confidence"] == 0.95
        assert parsed["candidates_per_iteration"] == 2

        # Verify failed_approaches
        assert parsed["failed_approaches"] == [
            "Direct computation diverges",
            "Induction base case fails",
        ]

        # Verify events
        assert len(parsed["events"]) == 3
        assert parsed["events"][0]["type"] == "generate"
        assert parsed["events"][0]["iteration"] == 1
        assert parsed["events"][0]["timestamp"] == 1000.0
        assert parsed["events"][0]["candidate"] == 1
        assert parsed["events"][0]["solution_preview"] == "Let x = ..."
        assert parsed["events"][1]["type"] == "verify"
        assert parsed["events"][1]["confidence"] == 0.95
        assert parsed["events"][2]["type"] == "accept"
        assert parsed["events"][2]["final_confidence"] == 0.95


class TestReviserSectionTargeting:
    def test_reviser_includes_low_confidence_sections(self):
        from alethic.subagents import revise

        # Create a mock client
        client = MagicMock()
        response = MagicMock()
        text_block = MagicMock()
        text_block.text = "CHANGES MADE:\nFixed induction step.\n\nREVISED SOLUTION:\nRevised text."
        text_block.type = "text"
        response.content = [text_block]
        response.stop_reason = "end_turn"
        client.messages.create.return_value = response

        config = AgentConfig(enable_code_execution=False, verbose=False)
        solution = MagicMock()
        solution.solution_text = "Original solution text"
        solution.iteration = 1

        verification = VerificationResult(
            verdict=Verdict.MINOR_ISSUES,
            critique="Induction step is weak.",
            confidence=0.65,
            issues=[Issue(text="Induction hypothesis not applied correctly")],
            section_confidences=[
                SectionConfidence(section="Setup", confidence=0.95),
                SectionConfidence(section="Induction step", confidence=0.45, note="Shaky reasoning"),
            ],
        )

        revise(
            client,
            problem="Prove P(n) for all n",
            solution=solution,
            verification=verification,
            config=config,
            revision_number=1,
        )

        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        user_msg = messages[0]["content"]
        assert "Low-confidence sections" in user_msg
        assert "Induction step" in user_msg
        assert "0.45" in user_msg
