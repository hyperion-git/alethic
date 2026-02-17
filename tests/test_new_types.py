"""Tests for new types: Issue, SectionConfidence, AgentEvent, EventType, IssueSeverity.

Also tests updated VerificationResult (Issue objects, CRITICAL blocking),
AgentResult (events field, deprecated history, failed_approaches), and
the RunState/EventLog helpers.
"""

from __future__ import annotations

import time
import warnings

import pytest

from alethic.models import (
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

    def test_exactly_six_members(self):
        assert len(EventType) == 6


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
