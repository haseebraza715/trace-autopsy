"""Tests for analysis-agent quality validation helpers."""

from datetime import datetime

import pytest
from langchain_core.messages import AIMessageChunk

from agent_autopsy.analysis.agent import ReportQualityValidator
from agent_autopsy.analysis.llm_agent import AnalysisAgent
from agent_autopsy.schema import EnvironmentInfo, EventType, Trace, TraceEvent, TraceStatus


@pytest.fixture
def minimal_trace():
    start = datetime(2026, 1, 1)
    trace = Trace(
        run_id="wiring-test",
        timestamp_start=start,
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test"),
        events=[
            TraceEvent(
                event_id=0,
                type=EventType.LLM_CALL,
                name="gpt-4",
                input={"prompt": "hi"},
                output={"text": "hello"},
            )
        ],
    )
    trace.stats = trace.calculate_stats()
    return trace


class TestReportQualityValidator:
    """Quality-gate validation tests."""

    def test_validate_high_quality_report(self):
        report = """
## Summary
The run failed at Event 14 due to repeated tool retries.

## Timeline
- Event 7: first timeout
- Events 12-14: repeated retries without backoff

## Root Cause Chain
Root cause: missing retry policy guard. Event 12 retried after the same timeout.

## Fix Recommendations
- Add exponential backoff and retry cap at the tool wrapper.
- Implement circuit breaker after 3 failures.

## Confidence
0.82 based on repeated evidence in Events 12-14.
"""
        quality = ReportQualityValidator.validate(report)

        assert quality["overall_score"] >= 0.7
        assert quality["has_event_citations"] is True
        assert quality["has_root_cause"] is True
        assert quality["has_fix_recommendations"] is True

    def test_validate_low_quality_report_and_feedback(self):
        report = "Agent failed. Needs improvement."
        quality = ReportQualityValidator.validate(report)
        feedback = ReportQualityValidator.build_feedback(quality)

        assert quality["overall_score"] < 0.5
        assert len(quality["missing_sections"]) >= 3
        assert "missing sections" in feedback.lower()


class TestAnalysisAgentStreamHelpers:
    """Helpers used by LangGraph streaming UI."""

    def test_stringify_stream_content(self) -> None:
        assert AnalysisAgent._stringify_stream_content("hi") == "hi"
        assert AnalysisAgent._stringify_stream_content(
            [{"type": "text", "text": "ab"}]
        ) == "ab"
        assert AnalysisAgent._stringify_stream_content(["x", "y"]) == "xy"

    def test_stream_chat_to_message_accumulates_chunks(self) -> None:
        """Runnable.stream chunks merge to a single AIMessage (no LangGraph writer)."""

        class _Runnable:
            def stream(self, _messages: object):
                yield AIMessageChunk(content="Hel")
                yield AIMessageChunk(content="lo")

        class _Dummy:
            def _optional_stream_writer(self):
                return None

        dummy = _Dummy()
        out = AnalysisAgent._stream_chat_to_message(dummy, _Runnable(), [], node_name="test")
        text = out.content if isinstance(out.content, str) else str(out.content)
        assert "Hello" in text


class TestProviderModelWiring:
    """init_chat_model must receive the bare model slug when model_provider
    is explicit; a prefixed string reaches the wire verbatim and 400s."""

    def _capture(self, monkeypatch):
        captured = {}

        def fake_init(model, **kwargs):
            captured["model"] = model
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr("langchain.chat_models.init_chat_model", fake_init)
        return captured

    def _agent(self, trace, provider, api_key="test-key"):
        from agent_autopsy.utils.config import Config, set_config

        set_config(Config(llm_provider=provider, openrouter_api_key=api_key))
        try:
            return AnalysisAgent(trace, model="stealth/ox-alpha")
        except Exception:
            return None

    def test_openrouter_gets_bare_slug(self, monkeypatch, minimal_trace):
        captured = self._capture(monkeypatch)
        self._agent(minimal_trace, "openrouter")
        assert captured["model"] == "stealth/ox-alpha"
        assert captured["kwargs"]["base_url"] is not None

    def test_openai_provider_gets_bare_slug(self, monkeypatch, minimal_trace):
        captured = self._capture(monkeypatch)
        self._agent(minimal_trace, "openai")
        assert captured["model"] == "stealth/ox-alpha"

    def test_anthropic_keeps_prefixed_model(self, monkeypatch, minimal_trace):
        captured = self._capture(monkeypatch)
        self._agent(minimal_trace, "anthropic")
        assert captured["model"] == "anthropic:stealth/ox-alpha"

    def test_ollama_keeps_prefixed_model(self, monkeypatch, minimal_trace):
        captured = self._capture(monkeypatch)
        self._agent(minimal_trace, "ollama")
        assert captured["model"] == "ollama:stealth/ox-alpha"
