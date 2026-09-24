"""
Tests that OpenTelemetry traces from different GenAI instrumentation libraries
(raw OTel GenAI SDK, OpenInference, traceloop/openllmetry) collapse to the
same canonical event after normalization.
"""

from pathlib import Path

import pytest

from src.ingestion import TraceNormalizer, parse_trace_file
from src.preanalysis.pricing import event_cost, event_token_split
from src.schema import EventType


FIXTURES = Path("tests/fixtures/otel_genai")


@pytest.fixture(params=[
    "otel_native_chat.json",
    "openinference_chat.json",
    "traceloop_chat.json",
])
def llm_event(request):
    """Parse one fixture and return the single LLM event."""
    trace = TraceNormalizer.normalize(parse_trace_file(FIXTURES / request.param))
    llm_events = [e for e in trace.events if e.type == EventType.LLM_CALL]
    assert len(llm_events) == 1, f"{request.param}: expected 1 LLM event, got {len(llm_events)}"
    return llm_events[0]


class TestCanonicalLLMEvent:
    """Each SDK dialect should yield the same canonical LLM event."""

    def test_event_type_is_llm_call(self, llm_event):
        assert llm_event.type == EventType.LLM_CALL

    def test_model_name_extracted(self, llm_event):
        assert llm_event.name == "gpt-4o"

    def test_token_count_is_total(self, llm_event):
        # 120 input + 30 output = 150 total
        assert llm_event.token_count == 150

    def test_token_split_recoverable(self, llm_event):
        split = event_token_split(llm_event)
        assert split is not None
        in_tok, out_tok = split
        assert in_tok == 120
        assert out_tok == 30

    def test_input_present(self, llm_event):
        assert llm_event.input is not None
        assert "2+2" in str(llm_event.input)

    def test_output_present(self, llm_event):
        assert llm_event.output is not None
        assert "4" in str(llm_event.output)

    def test_cost_computed(self, llm_event):
        # gpt-4o = $2.50 input / $10.00 output per 1M tokens
        # 120 * 2.50/1e6 + 30 * 10.00/1e6 = $0.0006
        cost = event_cost(llm_event)
        assert cost is not None
        assert cost == pytest.approx(0.0006, rel=1e-3)


class TestGenAIOperationDispatch:
    """`gen_ai.operation.name` should classify spans by type."""

    def test_chat_operation_yields_llm_call(self):
        trace = TraceNormalizer.normalize(parse_trace_file(FIXTURES / "otel_native_chat.json"))
        assert any(e.type == EventType.LLM_CALL for e in trace.events)
