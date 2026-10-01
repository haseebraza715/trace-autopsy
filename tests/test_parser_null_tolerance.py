"""Parser tolerance for explicit-null fields and non-textual attribute values.

Real exports carry ``null`` for optional fields (status, config, role,
llm_output, ...) and encode OTLP attrs numerically; neither may reject the
trace or invent failure status.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_autopsy.ingestion.parser import parse_trace_data
from agent_autopsy.schema import TraceStatus

_START_NS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9)


def _otel_doc(max_tokens_value) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "1",
                                "name": "llm call",
                                "startTimeUnixNano": str(_START_NS),
                                "endTimeUnixNano": str(_START_NS + 10**9),
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.max_tokens",
                                        "value": {"intValue": max_tokens_value},
                                    },
                                    {"key": "gen_ai.prompt", "value": {"stringValue": "hello"}},
                                    {"key": "gen_ai.completion", "value": {"stringValue": "hi there"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


class TestOpenTelemetryAttrValues:
    def test_numeric_int_value_does_not_reject_trace(self) -> None:
        trace = parse_trace_data(_otel_doc(8192))
        assert len(trace.events) == 1

    def test_non_textual_attr_never_becomes_event_input(self) -> None:
        trace = parse_trace_data(_otel_doc(8192))
        event = trace.events[0]
        assert event.input == "hello"

    def test_final_output_skips_non_textual_attrs(self) -> None:
        doc = _otel_doc(8192)
        doc["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
            {"key": "llm.result.length", "value": {"intValue": 8}}
        )
        trace = parse_trace_data(doc)
        assert trace.final_output == "hi there"


class TestLangGraphNullFields:
    def test_null_status_parses(self) -> None:
        doc = {
            "thread_id": "t1",
            "checkpoint": {},
            "status": None,
            "events": [{"type": "llm_call", "name": "chat", "timestamp": "2026-01-01T00:00:00Z"}],
        }
        trace = parse_trace_data(doc)
        assert trace.status == TraceStatus.SUCCESS

    def test_null_config_input_task_parse(self) -> None:
        doc = {
            "thread_id": "t2",
            "config": None,
            "input": None,
            "task": None,
            "events": [{"type": "tool_call", "name": "fetch", "timestamp": "2026-01-01T00:00:00Z"}],
        }
        trace = parse_trace_data(doc)
        assert len(trace.events) == 1

    def test_explicit_null_error_keeps_success_status(self) -> None:
        doc = {
            "thread_id": "t3",
            "error": None,
            "events": [
                {
                    "type": "llm_call",
                    "name": "chat",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "error": None,
                }
            ],
        }
        trace = parse_trace_data(doc)
        assert trace.status == TraceStatus.SUCCESS


class TestLangChainNullFields:
    def test_null_llm_output_and_response_metadata_parse(self) -> None:
        doc = {
            "runs": [
                {
                    "run_id": "r2",
                    "run_type": "llm",
                    "name": "gpt",
                    "start_time": 1767225600000,
                    "end_time": 1767225601000,
                    "inputs": {"prompt": "hi"},
                    "llm_output": None,
                    "response_metadata": None,
                }
            ]
        }
        trace = parse_trace_data(doc)
        assert len(trace.events) == 1

    def test_null_serialized_with_missing_name_parses(self) -> None:
        doc = {
            "runs": [
                {
                    "run_id": "r3",
                    "run_type": "chain",
                    "start_time": 1767225600000,
                    "end_time": 1767225601000,
                    "serialized": None,
                    "inputs": {},
                    "outputs": {},
                }
            ]
        }
        trace = parse_trace_data(doc)
        assert len(trace.events) == 1

    def test_top_level_null_error_keeps_success_status(self) -> None:
        doc = {
            "runs": [
                {
                    "run_id": "r4",
                    "run_type": "chain",
                    "name": "root",
                    "start_time": 1767225600000,
                    "end_time": 1767225601000,
                    "inputs": {},
                    "outputs": {"ok": True},
                    "error": None,
                }
            ]
        }
        trace = parse_trace_data(doc)
        assert trace.status == TraceStatus.SUCCESS

    def test_boolean_token_attr_does_not_become_token_count(self) -> None:
        doc = _otel_doc(8192)
        attrs = doc["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
        del attrs[0]
        attrs.insert(0, {"key": "token.streaming", "value": {"boolValue": True}})
        trace = parse_trace_data(doc)
        assert trace.events[0].token_count is None
        assert trace.events[0].input == "hello"


class TestLangGraphNullMessageFields:
    def test_null_type_name_role_message_event_parses(self) -> None:
        doc = {
            "thread_id": "t4",
            "events": [
                {"type": None, "name": None, "role": None, "content": "hello"},
                {"type": "tool_call", "name": "fetch", "timestamp": "2026-01-01T00:00:00Z"},
            ],
        }
        trace = parse_trace_data(doc)
        assert len(trace.events) == 2


class TestLangChainNullRunType:
    def test_null_run_type_alongside_truthy_runs_parse(self) -> None:
        doc = {
            "runs": [
                {
                    "run_id": "r5",
                    "run_type": None,
                    "name": "step",
                    "start_time": 1767225600000,
                    "end_time": 1767225601000,
                    "inputs": {},
                    "outputs": {},
                },
                {
                    "run_id": "r6",
                    "run_type": "chain",
                    "name": "root",
                    "start_time": 1767225600000,
                    "end_time": 1767225602000,
                    "inputs": {},
                    "outputs": {"done": True},
                },
            ]
        }
        trace = parse_trace_data(doc)
        assert len(trace.events) == 2

    def test_falsy_error_values_do_not_fail_status(self) -> None:
        for falsy in (None, False, ""):
            doc = {
                "thread_id": "t5",
                "error": falsy,
                "events": [
                    {
                        "type": "llm_call",
                        "name": "chat",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "error": falsy,
                    }
                ],
            }
            trace = parse_trace_data(doc)
            assert trace.status == TraceStatus.SUCCESS, f"error={falsy!r}"

    def test_final_output_skips_list_valued_attrs(self) -> None:
        doc = _otel_doc(8192)
        doc["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
            {"key": "llm.result.parts", "value": {"arrayValue": {"values": [
                {"stringValue": "part-1"}, {"intValue": "2"},
            ]}}}
        )
        trace = parse_trace_data(doc)
        assert trace.final_output == "hi there"
        assert trace.events[0].output == "hi there"
