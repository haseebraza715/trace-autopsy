"""Tests for report generation output quality."""

from datetime import datetime

from agent_autopsy.analysis.agent import AnalysisResult
from agent_autopsy.output import ReportGenerator
from agent_autopsy.output.deterministic_report import render_deterministic_markdown
from agent_autopsy.schema import (
    EnvironmentInfo,
    EventType,
    Trace,
    TraceEvent,
    TraceStatus,
)


def _trace_with_events() -> Trace:
    trace = Trace(
        run_id="report-test-run",
        timestamp_start=datetime(2026, 1, 1, 0, 0, 0),
        timestamp_end=datetime(2026, 1, 1, 0, 1, 0),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test", model="gpt-4"),
        events=[
            TraceEvent(event_id=0, type=EventType.DECISION, name="router", agent_id="planner"),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, output=None, agent_id="executor"),
            TraceEvent(event_id=2, type=EventType.ERROR, name="search", output="timeout"),
        ],
    )
    trace.stats = trace.calculate_stats()
    return trace


class TestReportGenerator:
    """Deterministic report behavior tests."""

    def test_health_score_and_timeline_markers(self):
        trace = _trace_with_events()
        result = AnalysisResult(
            report="",
            trace_summary={"total_events": 3, "errors": 1},
            preanalysis={
                "signals": [
                    {
                        "type": "timeout_pattern",
                        "severity": "high",
                        "evidence": "timeouts repeated",
                        "events": [1, 2],
                    }
                ],
                "top_suspects": [
                    {
                        "hypothesis": "Timeout bottleneck",
                        "confidence": 0.8,
                        "supporting_events": [1, 2],
                        "category": "ops",
                        "suggested_fixes": ["Set strict timeouts"],
                    }
                ],
            },
            success=True,
        )

        report = ReportGenerator(trace, result).generate()

        assert 0 <= report.health_score <= 100
        assert report.health_score < 100
        assert any(line.startswith("[001] !") or line.startswith("[002] X") for line in report.timeline)
        assert any("@planner" in line or "@executor" in line for line in report.timeline)

    def test_pattern_templates_are_added_to_fixes(self):
        trace = _trace_with_events()
        result = AnalysisResult(
            report="",
            trace_summary={"total_events": 3, "errors": 1},
            preanalysis={
                "signals": [
                    {"type": "infinite_loop", "severity": "critical", "events": [1, 2]},
                    {"type": "auth_permission_failure", "severity": "high", "events": [2]},
                ],
                "top_suspects": [],
            },
            success=True,
        )

        report = ReportGenerator(trace, result).generate()

        assert any("max_iterations" in item for item in report.fix_recommendations["code"])
        assert any("401/403" in item for item in report.fix_recommendations["ops"])


class TestReportDeduplication:
    """The synthesized report must not embed a second full report body."""

    def _result_with_deterministic_narrative(self):
        from agent_autopsy.preanalysis.suspects import RootCauseBuilder

        trace = _trace_with_events()
        bundle = RootCauseBuilder(trace).build()
        result = AnalysisResult(
            report=render_deterministic_markdown(trace, bundle),
            success=True,
            error=None,
            preanalysis=bundle.to_dict(),
            trace_summary=trace.calculate_stats().__dict__,
        )
        return ReportGenerator(trace, result)

    def test_markdown_contains_single_h1(self):
        gen = self._result_with_deterministic_narrative()
        md = gen.to_markdown()
        h1s = [line for line in md.splitlines() if line.startswith("# Autopsy Report")]
        assert len(h1s) == 1, h1s

    def test_status_line_appears_once(self):
        gen = self._result_with_deterministic_narrative()
        md = gen.to_markdown()
        status_lines = [line for line in md.splitlines() if line.startswith("- **Status:**")]
        assert len(status_lines) == 1, status_lines

    def test_findings_detail_is_kept(self):
        """Per-finding detail is the deterministic narrative's unique section."""
        gen = self._result_with_deterministic_narrative()
        md = gen.to_markdown()
        assert "**Likely cause" in md
        assert md.count("## Findings") == 0


class TestHealthScoreOverlapDamping:
    """Evidence cited by multiple signals must not stack penalties."""

    def _gen(self, signals):
        trace = _trace_with_events()
        return ReportGenerator(
            trace,
            AnalysisResult(
                report="",
                success=True,
                preanalysis={"signals": signals},
                trace_summary=trace.calculate_stats().__dict__,
            ),
        )

    def test_duplicate_evidence_does_not_stack_full_penalties(self):
        same_event = [
            {"type": "hallucinated_tool", "severity": "high", "events": [1]},
            {"type": "contract_unknown_tool", "severity": "high", "events": [1]},
        ]
        # one high penalty, then coverage: 1 of 3 events -> int(20/3)=6
        assert self._gen(same_event)._calculate_health_score() == 100 - 15 - 6

    def test_distinct_events_still_stack(self):
        distinct = [
            {"type": "a", "severity": "high", "events": [1]},
            {"type": "b", "severity": "medium", "events": [2]},
        ]
        # both stack, coverage: 2 of 3 events -> int(40/3)=13
        assert self._gen(distinct)._calculate_health_score() == 100 - 15 - 8 - 13

    def test_partial_overlap_pays_only_for_new_events(self):
        partial = [
            {"type": "cascade", "severity": "critical", "events": [0, 1, 2]},
            {"type": "storm", "severity": "high", "events": [1, 2]},
        ]
        # critical claims all three; storm has no new events left. Coverage 3/3 -> 20.
        assert self._gen(partial)._calculate_health_score() == 100 - 25 - 20

    def test_eventless_signals_keep_full_weight(self):
        signals = [{"type": "goal_drift", "severity": "medium", "events": []}]
        assert self._gen(signals)._calculate_health_score() == 100 - 8 - 0
