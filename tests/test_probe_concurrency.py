"""Concurrency and thread-safety probe tests for alethic.

Agent C -- Bug-Probing Swarm -- Wave 1.

Probes:
  C1  -- Partial failure in parallel generation (2/3 workers raise) -- should survive
  C2a -- Race condition: shared TokenLedger.record() called from parallel workers
  C2b -- EventLog.emit() called only from main thread (safe in practice)
  C3  -- Missing timeout on future.result() in _run_consensus() (hung verifier)
  C4  -- Shared mutable state safety in run_one() closure (verifier_agent.py)
  C5  -- Variant-B client created once before pool, not per-worker
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor as RealTPE
from unittest.mock import MagicMock, patch

import pytest

from alethic.agent import EventLog, MathAgent
from alethic.models import (
    AgentConfig,
    EventType,
    TokenLedger,
    Verdict,
    VerificationResult,
    VerifierConfig,
)
from alethic.verifier_agent import VerifierAgent


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_response(text: str):
    """Create a minimal mock Anthropic response with a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


CORRECT_HIGH = (
    "VERDICT: correct\nCONFIDENCE: 0.95\n\nCRITIQUE:\nPerfect.\n\nISSUES:\nNone"
)
MAJOR_FLAW = (
    "VERDICT: major_flaw\nCONFIDENCE: 0.20\n\nCRITIQUE:\nWrong.\n\nISSUES:\n- Logic error"
)


# ---------------------------------------------------------------------------
# Probe C1: Partial failure in parallel generation
# ---------------------------------------------------------------------------


class TestProbeC1PartialGenerationFailure:
    """C1 -- When 2 of 3 parallel generate() calls raise, the orchestrator should
    continue with the 1 survivor rather than propagating the exception or
    returning an empty result.

    The fix is already present: _generate_candidates() catches exceptions
    per-future inside as_completed(). These tests document that correct
    behavior and would catch a regression where exceptions bubble up.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c1_two_of_three_fail_survives(self, _mock_tools):
        """Two out of three parallel generate() calls raise; the one survivor
        should be verified and accepted, making solve() succeed.

        If _generate_candidates() propagated the first exception (e.g., via
        future.result() without try/except), this would raise RuntimeError
        and solve() would crash. The test asserts it does not.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        call_counts = {"n": 0}
        lock = threading.Lock()

        def side_effect(*args, **kwargs):
            with lock:
                call_counts["n"] += 1
                n = call_counts["n"]
            if n == 1:
                raise RuntimeError("Simulated API failure on candidate 1")
            elif n == 2:
                raise RuntimeError("Simulated API failure on candidate 2")
            elif n == 3:
                return _mock_response("Survivor candidate solution")
            else:
                # Verify call for the survivor
                return _mock_response(CORRECT_HIGH)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Is 1+1=2?")

        assert result.solved, (
            "Expected solve() to succeed with 1/3 surviving candidates, "
            f"got verdict={result.verdict}, confidence={result.confidence}"
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c1_all_fail_emits_error_event(self, _mock_tools):
        """When ALL parallel generate() calls fail, the iteration is skipped
        (not crashed) and an ERROR event is logged. With max_iterations=1,
        the agent returns UNSOLVED.
        """
        config = AgentConfig(
            max_iterations=1,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("All generators failed")

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Prove Riemann hypothesis")

        assert not result.solved
        assert result.verdict == Verdict.UNSOLVED
        error_events = [e for e in result.events if e.type == EventType.ERROR]
        assert len(error_events) >= 1, (
            "Expected at least one ERROR event when all candidates fail"
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c1_partial_failure_logs_n_failed(self, _mock_tools):
        """Partial generation failure should emit an ERROR event recording how
        many candidates failed, not crash the solve loop.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        call_counts = {"n": 0}
        lock = threading.Lock()

        def side_effect(*args, **kwargs):
            with lock:
                call_counts["n"] += 1
                n = call_counts["n"]
            if n <= 2:
                raise RuntimeError(f"Candidate {n} failed")
            elif n == 3:
                return _mock_response("Survivor")
            else:
                return _mock_response(CORRECT_HIGH)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        agent = MathAgent(config=config)
        agent.client = mock_client

        result = agent.solve("Is P=NP?")

        error_events = [e for e in result.events if e.type == EventType.ERROR]
        partial_error = any(
            "candidates failed" in str(e.data.get("error", ""))
            for e in error_events
        )
        assert partial_error, (
            f"Expected an ERROR event mentioning partial candidate failures; "
            f"got error events: {[e.data for e in error_events]}"
        )


# ---------------------------------------------------------------------------
# Probe C2a: Race condition in shared TokenLedger
# ---------------------------------------------------------------------------


class TestProbeC2aTokenLedgerConcurrency:
    """C2a -- TokenLedger.record() is called from multiple parallel worker threads.

    The shared TokenLedger is passed into _generate_candidates() as `ledger`
    and forwarded via the _gen_one() closure into each parallel generate()
    -> _call_model() call. Each _call_model() call executes:

        self.input_tokens  += usage.input_tokens   # LOAD_ATTR, BINARY_OP, STORE_ATTR
        self.output_tokens += usage.output_tokens
        self.api_calls     += 1

    These three-step sequences are NOT atomic in CPython: the GIL can release
    between bytecodes, enabling a classic lost-update race between parallel
    workers. Token counts may silently undercount under N>1 generation.

    A correct fix: add threading.Lock() to TokenLedger and acquire it in record().
    """

    def test_probe_c2a_token_ledger_stress(self):
        """Stress-test TokenLedger.record() under heavy concurrency.

        50 workers each call record() 100 times, recording 1 input and
        1 output token each time. Expected totals: 5000 each.

        With a race condition, actual totals may be lower (lost updates).
        This test asserts correctness -- if it starts flaking on CI, the
        race has manifested. On free-threaded Python (PEP 703, no GIL)
        this would fail reliably.
        """
        ledger = TokenLedger()
        n_workers = 50
        calls_per_worker = 100

        def fake_usage():
            u = MagicMock()
            u.input_tokens = 1
            u.output_tokens = 1
            return u

        def worker():
            for _ in range(calls_per_worker):
                ledger.record(fake_usage())

        threads = [threading.Thread(target=worker) for _ in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_workers * calls_per_worker
        assert ledger.api_calls == expected, (
            f"TokenLedger.api_calls race condition: expected {expected}, "
            f"got {ledger.api_calls}. Lost {expected - ledger.api_calls} increments."
        )
        assert ledger.input_tokens == expected, (
            f"TokenLedger.input_tokens race: expected {expected}, "
            f"got {ledger.input_tokens}."
        )
        assert ledger.output_tokens == expected, (
            f"TokenLedger.output_tokens race: expected {expected}, "
            f"got {ledger.output_tokens}."
        )

    def test_probe_c2a_shared_ledger_passed_to_all_workers(self):
        """Verify that the SAME TokenLedger instance is passed into all
        parallel generate() calls inside _generate_candidates().

        This confirms the architecture: if record() is called from N parallel
        threads on the same object, the non-atomic += operations are a
        structural race condition regardless of CPython GIL behavior.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        ledger_ids_seen: list[int] = []
        lock = threading.Lock()

        def capturing_generate(client, problem, config, iteration, **kwargs):
            """Intercept generate() to capture the ledger identity."""
            ledger = kwargs.get("ledger")
            if ledger is not None:
                with lock:
                    ledger_ids_seen.append(id(ledger))
            from alethic.models import Solution
            return Solution(problem=problem, solution_text="dummy", iteration=iteration)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_response(CORRECT_HIGH)

        agent = MathAgent(config=config)
        agent.client = mock_client

        with patch("alethic.agent.generate", side_effect=capturing_generate):
            agent.solve("Is 2 prime?")

        assert len(ledger_ids_seen) == 3, (
            f"Expected 3 generate() calls (best_of_n=3), got {len(ledger_ids_seen)}"
        )
        unique_ids = set(ledger_ids_seen)
        assert len(unique_ids) == 1, (
            f"Expected all workers to share ONE TokenLedger instance. "
            f"Got {len(unique_ids)} distinct ledger objects: {unique_ids}. "
            f"All parallel workers share the same mutable TokenLedger -- "
            f"concurrent record() calls are a structural race condition."
        )


# ---------------------------------------------------------------------------
# Probe C2b: EventLog thread safety
# ---------------------------------------------------------------------------


class TestProbeC2bEventLogThreadSafety:
    """C2b -- EventLog.emit() uses list.append(), which is atomic in CPython.
    More importantly, EventLog is only called from the main orchestrator
    thread -- NOT from ThreadPoolExecutor workers -- so concurrent access
    does not occur in practice.

    These tests document this safe behavior and catch regressions.
    """

    def test_probe_c2b_event_log_emit_accumulates_all_events(self):
        """EventLog.emit() accumulates all events correctly (no lost entries)."""
        log = EventLog()
        n = 100
        for i in range(n):
            log.emit(EventType.GENERATE, iteration=i, candidate=i)

        assert len(log.events) == n, (
            f"Expected {n} events in EventLog, got {len(log.events)}"
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c2b_emit_called_only_from_main_thread(self, _mock_tools):
        """Verify that EventLog.emit() is never called from inside worker threads.

        If workers started emitting events directly (e.g., to log candidate-
        level details), concurrent list.append() calls would occur. This test
        catches that regression by recording which thread calls emit().
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("A"),
            _mock_response("B"),
            _mock_response("C"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
        ]

        threads_that_emitted: list[str] = []
        original_emit = EventLog.emit

        def capturing_emit(self_log, type, iteration, **data):
            threads_that_emitted.append(threading.current_thread().name)
            return original_emit(self_log, type, iteration, **data)

        agent = MathAgent(config=config)
        agent.client = mock_client

        with patch.object(EventLog, "emit", capturing_emit):
            agent.solve("Test")

        main_name = threading.main_thread().name
        non_main = [t for t in threads_that_emitted if t != main_name]
        assert len(non_main) == 0, (
            f"EventLog.emit() was called from non-main threads: {set(non_main)}. "
            f"Workers are directly emitting events -- concurrent append() risk."
        )


# ---------------------------------------------------------------------------
# Probe C3: Missing timeout on future.result() in parallel verification
# ---------------------------------------------------------------------------


class TestProbeC3VerifierTimeout:
    """C3 -- _run_consensus() (verifier_agent.py) calls future.result()
    with NO timeout argument.

    A hung verifier thread will:
    1. Block as_completed() from yielding that future indefinitely.
    2. Prevent ThreadPoolExecutor.__exit__ from completing (shutdown(wait=True)).
    3. Stall the entire pipeline with no recovery path.

    There is no TimeoutError handler, no cancellation, no escape hatch.

    A correct fix: future.result(timeout=N) + handle concurrent.futures.TimeoutError.
    """

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c3_pipeline_waits_for_slow_verifier(
        self, mock_verify, mock_synth
    ):
        """When one of K verifiers sleeps for 0.3s, the entire pipeline waits
        for it. This documents the absence of a per-future timeout.

        If a timeout were added (e.g., future.result(timeout=0.1)), the
        slow verifier would be abandoned and this test would need updating.
        """
        call_count = {"n": 0}

        def slow_then_fast(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                time.sleep(0.3)  # Simulated slow verifier
            return VerificationResult(
                verdict=Verdict.CORRECT, critique="ok", confidence=0.90
            )

        mock_verify.side_effect = slow_then_fast
        mock_synth.return_value = "Synthesized"

        config = VerifierConfig(num_verifiers=3, verbose=False, verification_ladder=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        t0 = time.time()
        result = agent.verify(problem="Is 1+1=2?", solution="Yes.")
        elapsed = time.time() - t0

        # Pipeline must have waited >= 0.3s for the slow verifier
        assert elapsed >= 0.25, (
            f"Pipeline completed in {elapsed:.3f}s -- faster than the 0.3s slow "
            f"verifier delay. This implies a timeout aborted it early. "
            f"No such timeout exists in the current code."
        )
        assert result.num_verifiers == 3

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c3_no_timeout_in_source(self, mock_verify, mock_synth):
        """Inspect _run_consensus source to confirm future.result() has no
        timeout argument. If a timeout is ever added, this test documents
        the change and should be updated.
        """
        import inspect

        source = inspect.getsource(VerifierAgent._run_consensus)

        # Current code must NOT have a timeout on future.result()
        assert "future.result(timeout" not in source, (
            "future.result() now has a timeout argument in _run_consensus(). "
            "This is a GOOD improvement -- update this test to confirm the new behavior."
        )

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c3_k_minus_1_fail_one_succeeds(self, mock_verify, mock_synth):
        """K-1 verifiers raise exceptions; 1 succeeds. Pipeline completes with
        1 result -- the per-future except clause in as_completed() works correctly.
        """
        call_count = {"n": 0}
        lock = threading.Lock()

        def raise_then_succeed(*args, **kwargs):
            with lock:
                call_count["n"] += 1
                n = call_count["n"]
            if n < 3:
                raise RuntimeError(f"Verifier {n} crashed")
            return VerificationResult(
                verdict=Verdict.CORRECT, critique="ok", confidence=0.88
            )

        mock_verify.side_effect = raise_then_succeed
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=3, verbose=False, verification_ladder=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        result = agent.verify(problem="Is 2 prime?", solution="Yes.")

        assert result.num_verifiers == 1, (
            f"Expected 1 successful verifier, got num_verifiers={result.num_verifiers}"
        )
        assert result.verdict == Verdict.CORRECT


# ---------------------------------------------------------------------------
# Probe C4: Shared mutable state safety in run_one() closure
# ---------------------------------------------------------------------------


class TestProbeC4RunOneClosureSafety:
    """C4 -- The run_one() closure in _run_consensus() captures:
    - self.client: shared anthropic.Anthropic (read-only reference)
    - problem: str (immutable)
    - sol: Solution dataclass (read-only)
    - agent_config: AgentConfig (frozen dataclass -- immutable)
    - system, user_template, extra_system: str|None (immutable)

    None are mutated inside run_one(). The results list is populated from
    the main thread inside as_completed(), not from workers. Safe.

    These tests document the correct behavior and catch regressions.
    """

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c4_k5_calls_return_independent_results(
        self, mock_verify, mock_synth
    ):
        """K=5 concurrent run_one() calls each produce an independent
        VerificationResult with a unique critique. No cross-contamination.
        """
        call_index = {"n": 0}
        lock = threading.Lock()

        def unique_result(*args, **kwargs):
            with lock:
                call_index["n"] += 1
                n = call_index["n"]
            return VerificationResult(
                verdict=Verdict.CORRECT,
                critique=f"unique critique #{n}",
                confidence=0.80 + n * 0.01,
            )

        mock_verify.side_effect = unique_result
        mock_synth.return_value = "Synthesized"

        config = VerifierConfig(num_verifiers=5, verbose=False, verification_ladder=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        result = agent.verify(problem="Test", solution="Answer")

        assert len(result.individual_results) == 5
        critiques = [r.critique for r in result.individual_results]
        unique_critiques = set(critiques)
        assert len(unique_critiques) == 5, (
            f"Expected 5 unique critiques (no state bleed), got {len(unique_critiques)}. "
            f"Critiques: {critiques}"
        )

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c4_closure_receives_correct_inputs_in_all_workers(
        self, mock_verify, mock_synth
    ):
        """All K workers should receive the same problem/solution that was
        passed to _run_consensus(). Mutable capture would cause divergence.
        """
        received_problems: list[str] = []
        received_solutions: list[str] = []
        lock = threading.Lock()

        def capture_inputs(client, problem, solution, config, **kwargs):
            with lock:
                received_problems.append(problem)
                received_solutions.append(solution.solution_text)
            return VerificationResult(
                verdict=Verdict.CORRECT, critique="ok", confidence=0.90
            )

        mock_verify.side_effect = capture_inputs
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=3, verbose=False, verification_ladder=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        agent.verify(problem="What is 2+2?", solution="2+2=4 by Peano axioms.")

        assert len(received_problems) == 3
        assert all(p == "What is 2+2?" for p in received_problems), (
            f"Workers saw different problem texts: {received_problems}"
        )
        assert all(s == "2+2=4 by Peano axioms." for s in received_solutions), (
            f"Workers saw different solution texts: {received_solutions}"
        )

    @patch("alethic.verifier_agent.synthesize_critique")
    @patch("alethic.verifier_agent.verify_subagent")
    def test_probe_c4_results_count_matches_k(self, mock_verify, mock_synth):
        """num_verifiers in ConsensusResult equals K -- the results list is
        populated correctly by the main thread from as_completed(), not by
        workers racing to append.
        """
        mock_verify.return_value = VerificationResult(
            verdict=Verdict.CORRECT, critique="ok", confidence=0.90
        )
        mock_synth.return_value = "ok"

        config = VerifierConfig(num_verifiers=4, verbose=False, verification_ladder=False)
        agent = VerifierAgent(config=config, api_key="test-key")

        result = agent.verify(problem="Test", solution="Test answer")

        assert result.num_verifiers == 4
        assert len(result.individual_results) == 4


# ---------------------------------------------------------------------------
# Probe C5: Variant-B client creation under concurrency
# ---------------------------------------------------------------------------


class TestProbeC5VariantBClientCreation:
    """C5 -- In _generate_candidates() (agent.py), the variant-B
    Anthropic client is created BEFORE the ThreadPoolExecutor starts.

    The pre-built client reference is passed to workers as a _gen_one() argument.
    Workers do NOT create the client themselves. If they did, concurrent
    anthropic.Anthropic() calls could race on module-level initialization state.
    """

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c5_different_model_creates_client_exactly_once(
        self, _mock_tools
    ):
        """With best_of_n=4 and variant_b using a different model, Anthropic()
        should be called exactly ONCE -- in the main thread before the pool,
        not once per odd-indexed worker.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=4,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )

        primary_client = MagicMock(name="primary_client")
        variant_client = MagicMock(name="variant_client")

        primary_client.messages.create.side_effect = [
            _mock_response("Candidate A (primary)"),
            _mock_response("Candidate C (primary)"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
        ]
        variant_client.messages.create.side_effect = [
            _mock_response("Candidate B (variant)"),
            _mock_response("Candidate D (variant)"),
        ]

        agent = MathAgent(config=config)
        agent.client = primary_client
        agent._api_key = "test-key"

        with patch(
            "alethic.agent.anthropic.Anthropic", return_value=variant_client
        ) as mock_cls:
            result = agent.solve("Test problem")

        mock_cls.assert_called_once_with(api_key="test-key"), (
            f"Expected exactly 1 call to anthropic.Anthropic() for variant-B. "
            f"Got {mock_cls.call_count} calls. Multiple calls would indicate "
            f"per-worker client creation -- a concurrency hazard."
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c5_same_model_never_creates_new_client(self, _mock_tools):
        """When variant_b uses the SAME model as primary, no new Anthropic()
        client is created -- the primary client is reused for all workers.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=4,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-opus-4-6"},  # Same as default primary
        )

        mock_client = MagicMock(name="shared_client")
        mock_client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response("Candidate B"),
            _mock_response("Candidate C"),
            _mock_response("Candidate D"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client
        agent._api_key = "test-key"

        with patch("alethic.agent.anthropic.Anthropic") as mock_cls:
            agent.solve("Test problem")

        mock_cls.assert_not_called(), (
            f"Expected zero Anthropic() calls when variant_b uses the same model. "
            f"Got {mock_cls.call_count} calls."
        )

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c5_no_variant_b_no_client_creation(self, _mock_tools):
        """When variant_b is None (default), no variant-B client is ever
        created, regardless of best_of_n.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=3,
            enable_code_execution=False,
            verbose=False,
            variant_b=None,
        )

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _mock_response("A"),
            _mock_response("B"),
            _mock_response("C"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
        ]

        agent = MathAgent(config=config)
        agent.client = mock_client
        agent._api_key = "test-key"

        with patch("alethic.agent.anthropic.Anthropic") as mock_cls:
            result = agent.solve("Test problem")

        mock_cls.assert_not_called()
        assert result.solved

    @patch("alethic.subagents.process_tool_calls", return_value=[])
    def test_probe_c5_client_created_in_main_thread_before_pool(
        self, _mock_tools
    ):
        """Verify the variant-B client is created in the main thread before
        the ThreadPoolExecutor starts -- not lazily inside worker threads.

        Tracks the thread name at the point anthropic.Anthropic() is called
        and asserts it is always the main thread.
        """
        config = AgentConfig(
            max_iterations=1,
            max_revisions_per_cycle=0,
            best_of_n=2,
            enable_code_execution=False,
            verbose=False,
            variant_b={"model": "claude-sonnet-4-6"},
        )

        primary_client = MagicMock(name="primary")
        variant_client = MagicMock(name="variant")

        primary_client.messages.create.side_effect = [
            _mock_response("Candidate A"),
            _mock_response(CORRECT_HIGH),
            _mock_response(CORRECT_HIGH),
        ]
        variant_client.messages.create.side_effect = [
            _mock_response("Candidate B"),
        ]

        agent = MathAgent(config=config)
        agent.client = primary_client
        agent._api_key = "test-key"

        anthropic_creation_threads: list[str] = []
        pool_start_threads: list[str] = []

        class TrackingTPE(RealTPE):
            def __init__(self, *args, **kwargs):
                pool_start_threads.append(threading.current_thread().name)
                super().__init__(*args, **kwargs)

        def tracking_anthropic(api_key=None):
            anthropic_creation_threads.append(threading.current_thread().name)
            return variant_client

        with (
            patch("alethic.agent.anthropic.Anthropic", side_effect=tracking_anthropic),
            patch("alethic.agent.ThreadPoolExecutor", TrackingTPE),
        ):
            agent.solve("Test")

        main_name = threading.main_thread().name

        assert len(anthropic_creation_threads) >= 1, (
            "anthropic.Anthropic() was never called -- variant_b path not taken"
        )
        non_main_creation = [t for t in anthropic_creation_threads if t != main_name]
        assert len(non_main_creation) == 0, (
            f"anthropic.Anthropic() was called from non-main threads: "
            f"{non_main_creation}. Per-worker client creation is a concurrency bug."
        )
