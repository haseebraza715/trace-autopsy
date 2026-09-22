"""Tests for src/advanced/reproduce.py using a mock LLM invoker."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.advanced.reproduce import Reproducer
from src.schema import (
    EnvironmentInfo,
    EventError,
    EventType,
    Trace,
    TraceEvent,
    TraceStatus,
)


def _trace_with_loop() -> Trace:
    """Trace with an LLM call followed by a tool call repeated 4× — triggers infinite_loop."""
    events: list[TraceEvent] = [
        TraceEvent(
            event_id=0,
            type=EventType.LLM_CALL,
            name="gpt-4o",
            input="What is the weather?",
            output="I'll search for weather.",
            token_count=80,
        ),
    ]
    # Four identical tool calls → infinite_loop pattern fires (threshold=3)
    for i in range(1, 5):
        events.append(
            TraceEvent(
                event_id=i,
                type=EventType.TOOL_CALL,
                name="web_search",
                input={"query": "weather NY"},
                output=None,
                error=EventError(message="connection timeout"),
            )
        )
    return Trace(
        run_id="repro_test",
        timestamp_start=datetime(2026, 1, 1),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test", model="gpt-4o"),
        events=events,
    )


def _empty_response_trace() -> Trace:
    """LLM call returning '' → empty_response pattern fires."""
    return Trace(
        run_id="empty_test",
        timestamp_start=datetime(2026, 1, 1),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test", model="gpt-4o"),
        events=[
            TraceEvent(event_id=0, type=EventType.LLM_CALL, name="gpt-4o",
                       input="hello", output="", token_count=5),
        ],
    )


class TestReproducer:
    def test_persistent_pattern_when_failure_is_not_in_llm_output(self):
        """Loop is in tool calls, not LLM output. Replay shouldn't clear it."""
        trace = _trace_with_loop()
        invoker = lambda model, prompt: "different reasoning, but tools still loop"
        result = Reproducer(trace, invoker=invoker).reproduce(n=3)
        assert result.runs == 3
        assert "infinite_loop" in result.persistent_patterns
        assert result.reproduce_rate == 1.0

    def test_cleared_pattern_when_new_output_breaks_pattern(self):
        """Empty-response pattern: if new output is non-empty, pattern clears."""
        trace = _empty_response_trace()
        invoker = lambda model, prompt: "Here is a real response."
        result = Reproducer(trace, invoker=invoker).reproduce(n=2)
        assert "empty_response" in result.cleared_patterns
        assert result.reproduce_rate == 0.0

    def test_per_event_outputs_recorded(self):
        trace = _empty_response_trace()
        invoker = lambda model, prompt: f"reply to: {prompt}"
        result = Reproducer(trace, invoker=invoker).reproduce(n=2)
        assert len(result.per_event) == 1
        assert result.per_event[0].new_outputs == ["reply to: hello", "reply to: hello"]
        assert result.per_event[0].original_output == ""

    def test_invoker_errors_are_collected(self):
        trace = _empty_response_trace()
        def bad_invoker(model, prompt):
            raise RuntimeError("network down")
        result = Reproducer(trace, invoker=bad_invoker).reproduce(n=1)
        assert result.per_event[0].errors == ["network down"]
        assert result.per_event[0].new_outputs == []

    def test_model_override_used_for_all_events(self):
        trace = _trace_with_loop()
        seen_models: list[str] = []
        def invoker(model, prompt):
            seen_models.append(model)
            return "ok"
        Reproducer(trace, invoker=invoker, model_override="gpt-4o-mini").reproduce(n=1)
        assert all(m == "gpt-4o-mini" for m in seen_models)

    def test_reproduces_count_partial(self):
        """Inject a flaky invoker so some runs reproduce and others don't."""
        trace = _empty_response_trace()
        outputs = iter(["", "real response", ""])
        invoker = lambda model, prompt: next(outputs)
        result = Reproducer(trace, invoker=invoker).reproduce(n=3)
        assert result.runs == 3
        # Two runs returned "" (empty_response fires); one returned a real response.
        assert result.reproduced_count == 2
        assert result.reproduce_rate == pytest.approx(2 / 3, rel=1e-3)
        # Pattern fired in 2 out of 3 runs → not in every run, so not persistent
        assert "empty_response" not in result.persistent_patterns
        # And it did fire somewhere → not cleared either
        assert "empty_response" not in result.cleared_patterns

    def test_summary_line_includes_rate(self):
        trace = _empty_response_trace()
        invoker = lambda model, prompt: "ok"
        result = Reproducer(trace, invoker=invoker).reproduce(n=2)
        line = result.summary_line()
        assert "0/2" in line and "0%" in line

    def test_dict_input_serialized_to_prompt(self):
        """Tool-style input dicts get JSON-serialized for the prompt."""
        trace = Trace(
            run_id="dict_input",
            timestamp_start=datetime(2026, 1, 1),
            status=TraceStatus.FAILED,
            env=EnvironmentInfo(agent_framework="test", model="gpt-4o"),
            events=[
                TraceEvent(event_id=0, type=EventType.LLM_CALL, name="gpt-4o",
                           input={"messages": [{"role": "user", "content": "hi"}]},
                           output="hello"),
            ],
        )
        seen_prompts: list[str] = []
        def invoker(model, prompt):
            seen_prompts.append(prompt)
            return "x"
        Reproducer(trace, invoker=invoker).reproduce(n=1)
        # Prompt should be JSON-serialized dict
        parsed = json.loads(seen_prompts[0])
        assert parsed == {"messages": [{"role": "user", "content": "hi"}]}
