"""Tests for advanced deterministic fix generation."""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_autopsy.output import FixSuggestionGenerator
from agent_autopsy.preanalysis import RootCauseBuilder
from agent_autopsy.schema import EnvironmentInfo, EventType, Trace, TraceEvent, TraceStatus


def _loop_trace() -> Trace:
    trace = Trace(
        run_id="fix-trace",
        timestamp_start=datetime(2026, 1, 1, 0, 0, 0),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test", tools_available=["search"]),
        events=[
            TraceEvent(event_id=0, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, output={"ok": 1}),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, output={"ok": 1}),
            TraceEvent(event_id=2, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, output={"ok": 1}),
            TraceEvent(event_id=3, type=EventType.ERROR, name="search", output="timeout"),
            TraceEvent(event_id=4, type=EventType.ERROR, name="search", output="still failing"),
        ],
    )
    trace.stats = trace.calculate_stats()
    return trace


def test_fix_generator_emits_loop_and_cascade_guidance():
    trace = _loop_trace()
    preanalysis = RootCauseBuilder(trace).build()

    suggestions = FixSuggestionGenerator(trace, preanalysis).to_dict()

    assert suggestions
    titles = {suggestion["title"] for suggestion in suggestions}
    assert "Add max-iteration guard to looping node" in titles
    assert any("error boundary" in title.lower() for title in titles)


@pytest.fixture
def sample_trace():
    return Trace(
        run_id="fix-gen-run",
        timestamp_start=datetime(2026, 1, 1, 0, 0, 0),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test", model="gpt-4", tools_available=["search"]),
        events=[
            TraceEvent(event_id=0, type=EventType.LLM_CALL, name="gpt-4", input={"q": "x"}, output={"text": "y"}),
            TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", input={"q": "x"}, output=None),
        ],
    )


class TestFullSignalCoverage:
    """Every deterministic signal type must yield at least one suggestion."""

    ALL_TYPES = [
        "infinite_loop",
        "retry_storm",
        "context_overflow",
        "hallucinated_tool",
        "empty_response",
        "error_cascade",
        "goal_drift",
        "stale_context",
        "token_waste",
        "auth_permission_failure",
        "timeout_pattern",
        "redundant_tool_call",
        "inter_agent_failure",
        "tool_contract_mismatch",
        "contract_unknown_tool",
        "contract_invalid_input",
        "contract_invalid_output",
        "contract_missing_metadata",
    ]

    def test_every_signal_type_produces_a_suggestion(self, sample_trace):
        from agent_autopsy.preanalysis import PreAnalysisBundle, Signal

        for signal_type in self.ALL_TYPES:
            bundle = PreAnalysisBundle(
                signals=[Signal(type=signal_type, severity="high", evidence="e", event_ids=[0])],
                hypotheses=[],
            )
            suggestions = FixSuggestionGenerator(sample_trace, bundle).to_dict()
            assert suggestions, f"no fix suggestion generated for {signal_type}"
            assert suggestions[0]["patch_snippet"].strip(), signal_type

    def test_contract_prefix_shares_the_tool_contract_advice(self, sample_trace):
        from agent_autopsy.preanalysis import PreAnalysisBundle, Signal

        def build(signal_type):
            return FixSuggestionGenerator(
                sample_trace,
                PreAnalysisBundle(
                    signals=[Signal(type=signal_type, severity="high", evidence="e", event_ids=[0])],
                    hypotheses=[],
                ),
            ).generate()

        mismatch = build("tool_contract_mismatch")[0]
        unknown = build("contract_unknown_tool")[0]
        invalid_output = build("contract_invalid_output")[0]
        missing_metadata = build("contract_missing_metadata")[0]
        assert unknown.category == mismatch.category
        assert invalid_output.patch_snippet != ""
        assert "instrument" in missing_metadata.rationale.lower() or "latency" in missing_metadata.patch_snippet.lower()
