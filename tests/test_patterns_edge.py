"""Pattern-detection edge cases: window clustering, thresholds, unicode, summaries."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent_autopsy.preanalysis import PatternDetector, PatternType, RootCauseBuilder, Severity
from agent_autopsy.schema import (
    EnvironmentInfo,
    EventError,
    EventType,
    TaskContext,
    Trace,
    TraceEvent,
    TraceStatus,
)
from agent_autopsy.utils.config import Config, set_config

T0 = datetime(2026, 1, 1, 0, 0, 0)


def _build_trace(
    events: list[TraceEvent],
    *,
    tools_available: list[str] | None = None,
    status: TraceStatus = TraceStatus.SUCCESS,
    task_goal: str | None = None,
) -> Trace:
    trace = Trace(
        run_id="edge-run",
        timestamp_start=T0,
        timestamp_end=T0 + timedelta(minutes=1),
        status=status,
        task=TaskContext(goal=task_goal) if task_goal else None,
        env=EnvironmentInfo(
            agent_framework="test",
            model="gpt-4",
            tools_available=tools_available or [],
        ),
        events=events,
    )
    trace.stats = trace.calculate_stats()
    return trace


def _tool_event(eid: int, name: str, ts: datetime, *, error: bool = False, query: str = "same") -> TraceEvent:
    return TraceEvent(
        event_id=eid,
        type=EventType.TOOL_CALL,
        name=name,
        input={"q": query},
        output={"ok": 1} if not error else None,
        error=EventError(message="failed") if error else None,
        timestamp=ts,
    )


class TestRetryStormChainedWindow:
    def test_calls_spaced_inside_window_chain_are_clustered(self) -> None:
        """5 calls spaced 40s apart with a 60s window must cluster as one
        retry storm (the window is chained, not anchored at the head)."""
        config = Config()
        config.retry_window_seconds = 60
        set_config(config)

        # Alternate inputs so the run is a retry storm, not a consecutive loop.
        queries = ["a", "b", "a", "b", "a"]
        events = [
            _tool_event(i, "search", T0 + timedelta(seconds=40 * i), error=True, query=queries[i])
            for i in range(5)
        ]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        storms = detector.detect_retry_storms()
        assert len(storms) == 1
        assert storms[0].pattern_type == PatternType.RETRY_STORM
        assert storms[0].metadata["count"] == 5
        assert storms[0].event_ids == [0, 1, 2, 3, 4]

    def test_window_break_does_not_bridge(self) -> None:
        config = Config()
        config.retry_window_seconds = 10
        set_config(config)

        queries = ["a", "b", "a", "b", "a"]
        events = [
            _tool_event(0, "search", T0, error=True, query=queries[0]),
            _tool_event(1, "search", T0 + timedelta(seconds=5), error=True, query=queries[1]),
            _tool_event(2, "search", T0 + timedelta(seconds=60), error=True, query=queries[2]),
            _tool_event(3, "search", T0 + timedelta(seconds=65), error=True, query=queries[3]),
            _tool_event(4, "search", T0 + timedelta(seconds=70), error=True, query=queries[4]),
        ]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        storms = detector.detect_retry_storms()
        assert len(storms) == 1
        assert storms[0].metadata["count"] == 3
        assert storms[0].event_ids == [2, 3, 4]

    def test_mixed_naive_aware_timestamps_do_not_crash(self) -> None:
        config = Config()
        config.retry_window_seconds = 60
        set_config(config)

        aware = T0.replace(tzinfo=datetime.now().astimezone().tzinfo)
        events = [
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, error=EventError(message="f"), timestamp=T0),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, error=EventError(message="f"), timestamp=aware),
            TraceEvent(event_id=2, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, error=EventError(message="f"), timestamp=aware + timedelta(seconds=1)),
        ]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        assert detector.detect_all()  # must not raise


class TestLoopThreshold:
    def test_configured_loop_threshold_applies(self) -> None:
        config = Config()
        config.loop_threshold = 2
        set_config(config)

        events = [_tool_event(i, "search", T0, error=True) for i in range(2)]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        loops = detector.detect_loops()
        assert len(loops) == 1
        assert loops[0].metadata["count"] == 2

    def test_default_threshold_still_three(self) -> None:
        set_config(Config())
        events = [_tool_event(i, "search", T0, error=True) for i in range(2)]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        assert detector.detect_loops() == []


class TestSummaryEdgeCases:
    def test_low_severity_only_summary_is_well_formed(self) -> None:
        events = [
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="search", output={"ok": 1}),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", output={"ok": 1}),
        ]
        trace = _build_trace(events, tools_available=["search"])
        bundle = RootCauseBuilder(trace).build()
        # Missing optional latency/token metadata is an informational note,
        # not a finding, so it must not appear in the signal summary.
        assert [n.type for n in bundle.notes] == ["contract_missing_metadata"] * 2
        assert bundle.signals == []
        assert bundle.summary == "No significant issues detected in trace."

    def test_empty_trace_summary(self) -> None:
        bundle = RootCauseBuilder(_build_trace([])).build()
        assert bundle.summary == "No significant issues detected in trace."
        assert bundle.to_dict()["summary"] == bundle.summary


class TestUnicodeAndContent:
    def test_unicode_tool_signatures_detect_loops(self) -> None:
        events = [
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="搜索", input={"查询": "预算"}, error=EventError(message="x")),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="搜索", input={"查询": "预算"}, error=EventError(message="x")),
            TraceEvent(event_id=2, type=EventType.TOOL_CALL, name="搜索", input={"查询": "预算"}, error=EventError(message="x")),
        ]
        detector = PatternDetector(_build_trace(events, status=TraceStatus.FAILED))
        loops = detector.detect_loops()
        assert len(loops) == 1
        assert loops[0].metadata["signature"].startswith("搜索:")

    def test_signature_digest_is_length_stable(self) -> None:
        event = TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="tool", input={"a": "x" * 10_000})
        sig = event.get_tool_signature()
        assert sig is not None
        assert len(sig) < 64  # name + 16-hex digest, not the full payload


class TestHallucinatedAndContractSignals:
    def test_unknown_tool_surfaces_both_signal_kinds(self) -> None:
        events = [
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="ghost_tool", input={"q": "x"}, output={"ok": 1}),
        ]
        trace = _build_trace(events, tools_available=["search"])
        bundle = RootCauseBuilder(trace).build()
        types = {s.type for s in bundle.signals}
        assert "hallucinated_tool" in types
        assert "contract_unknown_tool" in types

    def test_no_allowlist_skips_unknown_tool_signal(self) -> None:
        events = [
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="anything", input={"q": "x"}, output={"ok": 1}),
        ]
        trace = _build_trace(events, tools_available=[])
        bundle = RootCauseBuilder(trace).build()
        types = {s.type for s in bundle.signals}
        assert "hallucinated_tool" not in types
        assert "contract_unknown_tool" not in types


class TestGoalDriftLexical:
    def test_lexical_goal_drift_detected_without_embeddings(self) -> None:
        """Goal drift must work through lexical overlap when embeddings are skipped."""
        config = Config()
        config.skip_embeddings = True
        config.semantic_drift_enabled = True
        config.semantic_drift_delta_threshold = 0.35
        config.semantic_drift_low_threshold = 0.25
        set_config(config)

        goal = "create quarterly revenue report"
        events = [
            TraceEvent(event_id=i, type=EventType.LLM_CALL, name="gpt-4", input=f"revenue report step {i}", output="working on quarterly revenue")
            for i in range(4)
        ]
        events.extend(
            TraceEvent(event_id=i, type=EventType.LLM_CALL, name="gpt-4", input="tell me a joke about cats", output="joke")
            for i in range(4, 8)
        )
        trace = _build_trace(events, task_goal=goal)
        detector = PatternDetector(trace)
        drift = detector.detect_goal_drift()
        assert len(drift) == 1
        assert drift[0].pattern_type == PatternType.GOAL_DRIFT
        assert drift[0].metadata["method"] == "lexical_overlap"

    def test_no_goal_skips_drift(self) -> None:
        events = [TraceEvent(event_id=0, type=EventType.LLM_CALL, output="x")]
        detector = PatternDetector(_build_trace(events))
        assert detector.detect_goal_drift() == []


class TestSeverityGates:
    def test_successful_run_with_recovered_errors_still_flags_loop(self) -> None:
        """Recovered (successful) runs with errored calls are still flagged."""
        events = [_tool_event(i, "search", T0, error=True) for i in range(3)]
        trace = _build_trace(events, status=TraceStatus.SUCCESS, tools_available=["search"])
        assert len(PatternDetector(trace).detect_loops()) == 1

    def test_clean_successful_run_no_loop(self) -> None:
        events = [_tool_event(i, "search", T0) for i in range(3)]
        trace = _build_trace(events, status=TraceStatus.SUCCESS, tools_available=["search"])
        assert PatternDetector(trace).detect_loops() == []

    def test_severity_ordering_in_detect_all(self) -> None:
        config = Config()
        config.loop_threshold = 3
        set_config(config)
        events = [_tool_event(i, "search", T0, error=True) for i in range(3)]
        trace = _build_trace(events, status=TraceStatus.FAILED, tools_available=["search"])
        patterns = PatternDetector(trace).detect_all()
        kinds = [p.pattern_type for p in patterns]
        # infinite_loop runs first in detect_all
        assert kinds[0] == PatternType.INFINITE_LOOP
        assert Severity.CRITICAL in {p.severity for p in patterns}


class TestAuthDetectorFailureGate:
    def test_auth_language_in_clean_run_inputs_is_ignored(self) -> None:
        """Prompt text mentioning auth on a healthy run is not a failure signal."""
        events = [
            _tool_event(i, "docs", T0 + timedelta(seconds=i), query=f"explain oauth authorization flow {i}")
            for i in range(2)
        ]
        trace = _build_trace(events, status=TraceStatus.SUCCESS)
        results = PatternDetector(trace).detect_auth_permission_failures()
        assert results == []

    def test_auth_language_on_failed_run_still_fires(self) -> None:
        events = [
            _tool_event(i, "api", T0 + timedelta(seconds=i), query=f"request returned 401 unauthorized {i}")
            for i in range(2)
        ]
        trace = _build_trace(events, status=TraceStatus.FAILED)
        results = PatternDetector(trace).detect_auth_permission_failures()
        assert len(results) == 1
        assert results[0].pattern_type == PatternType.AUTH_PERMISSION_FAILURE


class TestRetryStormTimestampOrderingAndBursts:
    def test_descending_timestamps_do_not_chain_into_one_storm(self) -> None:
        """Out-of-order exporters must not glue distant calls into one storm."""
        base = datetime(2026, 1, 1, 12, 10, 0)
        events = [
            _tool_event(i, "fetch", base - timedelta(seconds=300 * i), query=f"attempt variant {i % 2}")
            for i in range(4)
        ]
        trace = _build_trace(events, status=TraceStatus.FAILED)
        storms = PatternDetector(trace).detect_retry_storms()
        assert storms == []

    def test_two_disjoint_bursts_are_both_reported(self) -> None:
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        events = []
        eid = 0
        for burst in range(2):
            for k, query in enumerate(["alpha", "alpha", "beta"]):
                events.append(_tool_event(eid, "fetch", t0 + timedelta(seconds=600 * burst + k), query=query))
                eid += 1
        trace = _build_trace(events, status=TraceStatus.FAILED)
        storms = PatternDetector(trace).detect_retry_storms()
        assert len(storms) == 2
        reported_ids = sorted(eid for s in storms for eid in s.event_ids)
        assert reported_ids == sorted(e.event_id for e in events)

    def test_positive_delta_chained_retries_still_form_one_storm(self) -> None:
        events = [_tool_event(i, "fetch", T0 + timedelta(seconds=30 * i), query=f"variant {i % 2}") for i in range(4)]
        trace = _build_trace(events, status=TraceStatus.FAILED)
        storms = PatternDetector(trace).detect_retry_storms()
        assert len(storms) == 1
        assert storms[0].event_ids == [0, 1, 2, 3]


class TestRetryStormSubClusterRecovery:
    def test_gate_rejected_cluster_still_yields_qualifying_tail(self) -> None:
        """A same-tool cluster rejected by the loop-overlap gate must not
        consume the rest of the scan window; a later qualifying burst of the
        same tool is still found."""
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        loop_part = [_tool_event(i, "fetch", t0 + timedelta(seconds=i), query="same") for i in range(3)]
        storm_part = [
            _tool_event(3, "fetch", t0 + timedelta(seconds=3), query="alpha"),
            _tool_event(4, "fetch", t0 + timedelta(seconds=4), query="alpha"),
            _tool_event(5, "fetch", t0 + timedelta(seconds=5), query="beta"),
        ]
        trace = _build_trace(loop_part + storm_part, status=TraceStatus.FAILED)
        storms = PatternDetector(trace).detect_retry_storms()
        assert len(storms) == 1
        assert storms[0].event_ids == [3, 4, 5]
