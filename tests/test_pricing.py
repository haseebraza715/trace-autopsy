"""Tests for src/preanalysis/pricing.py."""

from datetime import datetime

import pytest

from src.preanalysis.pricing import (
    compute_trace_cost,
    event_cost,
    event_token_split,
    format_cost_section,
    match_model,
    waste_event_ids_from_signals,
)
from src.schema import (
    EnvironmentInfo,
    EventType,
    Trace,
    TraceEvent,
    TraceStats,
    TraceStatus,
)


def _make_trace(events: list[TraceEvent], model: str = "gpt-4o") -> Trace:
    return Trace(
        run_id="test_run",
        timestamp_start=datetime(2026, 1, 1),
        timestamp_end=datetime(2026, 1, 1),
        status=TraceStatus.SUCCESS,
        env=EnvironmentInfo(agent_framework="test", model=model),
        events=events,
        stats=TraceStats(),
    )


class TestMatchModel:
    def test_strips_provider_prefix(self):
        match = match_model("openai/gpt-4o")
        assert match is not None
        key, _ = match
        assert key == "gpt-4o"

    def test_strips_openrouter_suffix(self):
        match = match_model("google/gemma-7b-it:free")
        assert match is not None
        key, _ = match
        assert key == "gemma"

    def test_picks_longest_match(self):
        # gpt-4o-mini contains both "gpt-4o" and "gpt-4o-mini" as candidate keys
        match = match_model("gpt-4o-mini-2024-07-18")
        assert match is not None
        key, _ = match
        assert key == "gpt-4o-mini"

    def test_unknown_model_returns_none(self):
        assert match_model("some-future-model-xyz") is None

    def test_empty_input_returns_none(self):
        assert match_model(None) is None
        assert match_model("") is None

    def test_anthropic_match(self):
        match = match_model("anthropic/claude-3-5-sonnet-20241022")
        assert match is not None
        assert match[0] == "claude-3-5-sonnet"

    def test_claude_4_match(self):
        match = match_model("claude-sonnet-4-20250514")
        assert match is not None
        assert match[0] == "claude-sonnet-4"


class TestEventTokenSplit:
    def test_uses_metadata_when_present(self):
        ev = TraceEvent(
            event_id=1,
            type=EventType.LLM_CALL,
            name="gpt-4o",
            token_count=200,
            metadata={"prompt_tokens": 150, "completion_tokens": 50},
        )
        assert event_token_split(ev) == (150, 50)

    def test_uses_otel_genai_keys(self):
        ev = TraceEvent(
            event_id=1,
            type=EventType.LLM_CALL,
            name="gpt-4o",
            metadata={"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 30},
        )
        assert event_token_split(ev) == (100, 30)

    def test_falls_back_to_token_count_70_30_split(self):
        ev = TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o", token_count=100)
        in_tok, out_tok = event_token_split(ev)
        assert in_tok == 70
        assert out_tok == 30
        assert in_tok + out_tok == 100

    def test_returns_none_when_no_token_info(self):
        ev = TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o")
        assert event_token_split(ev) is None


class TestEventCost:
    def test_non_llm_event_returns_none(self):
        ev = TraceEvent(event_id=1, type=EventType.TOOL_CALL, name="search", token_count=1000)
        assert event_cost(ev) is None

    def test_unknown_model_returns_none(self):
        ev = TraceEvent(event_id=1, type=EventType.LLM_CALL, name="mystery-model", token_count=1000)
        assert event_cost(ev) is None

    def test_priced_event_returns_dollars(self):
        # gpt-4o = $2.50 input, $10.00 output per 1M tokens
        # 1000 tokens × 70/30 split = 700 in × 2.50 + 300 out × 10.00 = $0.00475
        ev = TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000)
        cost = event_cost(ev)
        assert cost is not None
        assert cost == pytest.approx(0.00475, rel=1e-4)

    def test_uses_fallback_model_when_event_lacks_name(self):
        ev = TraceEvent(event_id=1, type=EventType.LLM_CALL, token_count=1000)
        cost = event_cost(ev, fallback_model="gpt-4o")
        assert cost is not None
        assert cost > 0


class TestComputeTraceCost:
    def test_empty_trace_zero_cost(self):
        trace = _make_trace([])
        breakdown = compute_trace_cost(trace)
        assert breakdown.total_usd == 0.0
        assert not breakdown.is_priced

    def test_sums_priced_events(self):
        events = [
            TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000),
            TraceEvent(event_id=2, type=EventType.LLM_CALL, name="gpt-4o", token_count=500),
        ]
        trace = _make_trace(events)
        breakdown = compute_trace_cost(trace)
        assert breakdown.priced_events == 2
        assert breakdown.total_usd == pytest.approx(0.00475 + 0.002375, rel=1e-3)
        assert "gpt-4o" in breakdown.by_model

    def test_counts_unpriced_events(self):
        events = [
            TraceEvent(event_id=1, type=EventType.LLM_CALL, name="mystery-llm", token_count=1000),
        ]
        trace = _make_trace(events)
        breakdown = compute_trace_cost(trace)
        assert breakdown.priced_events == 0
        assert breakdown.unpriced_events == 1

    def test_waste_total_separate_from_run_total(self):
        events = [
            TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000),
            TraceEvent(event_id=2, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000),
            TraceEvent(event_id=3, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000),
        ]
        trace = _make_trace(events)
        breakdown = compute_trace_cost(trace, waste_event_ids={2, 3})
        # Two-thirds of the events flagged as waste
        assert breakdown.waste_ratio == pytest.approx(2 / 3, rel=1e-3)
        assert breakdown.waste_usd < breakdown.total_usd

    def test_extrapolate_per_day(self):
        events = [TraceEvent(event_id=1, type=EventType.LLM_CALL, name="gpt-4o", token_count=1000)]
        trace = _make_trace(events)
        breakdown = compute_trace_cost(trace, waste_event_ids={1})
        assert breakdown.extrapolate_per_day(1000) == pytest.approx(breakdown.waste_usd * 1000)


class TestWasteEventIdsFromSignals:
    def test_extracts_from_dict_signals(self):
        signals = [
            {"type": "token_waste", "event_ids": [1, 2, 3]},
            {"type": "infinite_loop", "events": [4, 5]},  # alt key
            {"type": "auth_permission_failure", "event_ids": [9]},  # not waste
        ]
        ids = waste_event_ids_from_signals(signals)
        assert ids == {1, 2, 3, 4, 5}

    def test_handles_pattern_result_objects(self):
        from src.preanalysis import PatternResult, PatternType, Severity

        sigs = [
            PatternResult(
                pattern_type=PatternType.RETRY_STORM,
                severity=Severity.HIGH,
                message="m",
                evidence="e",
                event_ids=[7, 8],
            ),
            PatternResult(
                pattern_type=PatternType.INTER_AGENT_FAILURE,
                severity=Severity.HIGH,
                message="m",
                evidence="e",
                event_ids=[99],
            ),
        ]
        ids = waste_event_ids_from_signals(sigs)
        assert ids == {7, 8}


class TestFormatCostSection:
    def test_unpriced_returns_not_computed(self):
        from src.preanalysis.pricing import CostBreakdown

        out = format_cost_section(CostBreakdown())
        assert any("Not computed" in line for line in out)

    def test_priced_includes_run_cost(self):
        from src.preanalysis.pricing import CostBreakdown

        bd = CostBreakdown(total_usd=0.42, priced_events=3, by_model={"gpt-4o": 0.42})
        out = "\n".join(format_cost_section(bd))
        assert "Run cost" in out
        assert "0.4200" in out

    def test_priced_with_waste_shows_extrapolation(self):
        from src.preanalysis.pricing import CostBreakdown

        bd = CostBreakdown(
            total_usd=1.0,
            waste_usd=0.5,
            waste_ratio=0.5,
            priced_events=2,
            by_model={"gpt-4o": 1.0},
        )
        out = "\n".join(format_cost_section(bd, runs_per_day=2000))
        assert "Wasted" in out
        assert "Extrapolated" in out
        assert "2,000 runs/day" in out
