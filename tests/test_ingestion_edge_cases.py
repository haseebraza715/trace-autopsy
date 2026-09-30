"""Ingestion edge cases: malformed files, unicode, huge inputs, missing fields.

Every parser must degrade gracefully instead of raising untyped exceptions.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from agent_autopsy.errors import ParseError
from agent_autopsy.ingestion import TraceNormalizer, parse_trace_file
from agent_autopsy.ingestion.formats.generic import GenericJSONParser
from agent_autopsy.ingestion.formats.langgraph import LangGraphParser
from agent_autopsy.ingestion.formats.opentelemetry import OpenTelemetryParser
from agent_autopsy.ingestion.parser import parse_trace_data
from agent_autopsy.schema import EventType, TraceStatus

FIXTURES = Path(__file__).parent / "fixtures" / "e2e"


class TestMalformedFiles:
    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ParseError):
            parse_trace_file(p)

    def test_whitespace_only_file(self, tmp_path: Path) -> None:
        p = tmp_path / "ws.json"
        p.write_text("   \n\t  ", encoding="utf-8")
        with pytest.raises(ParseError):
            parse_trace_file(p)

    @pytest.mark.parametrize("root", ['["a", "b"]', '"just a string"', "42", "null", "true"])
    def test_non_object_root_rejected(self, tmp_path: Path, root: str) -> None:
        p = tmp_path / "root.json"
        p.write_text(root, encoding="utf-8")
        with pytest.raises(ParseError, match="JSON object"):
            parse_trace_file(p)

    def test_truncated_json(self, tmp_path: Path) -> None:
        p = tmp_path / "trunc.json"
        p.write_text('{"run_id": "x", "events": [{"type":', encoding="utf-8")
        with pytest.raises(ParseError, match="Invalid JSON"):
            parse_trace_file(p)

    def test_binary_garbage(self, tmp_path: Path) -> None:
        p = tmp_path / "bin.json"
        p.write_bytes(b"\x00\x01\x02\xff\xfe not json at all")
        with pytest.raises(ParseError):
            parse_trace_file(p)

    def test_unreadable_file_raises_parse_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = tmp_path / "locked.json"
        p.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text", lambda self, **kw: (_ for _ in ()).throw(OSError("denied")))
        with pytest.raises(ParseError, match="Could not read"):
            parse_trace_file(p)


class TestUnicode:
    def test_unicode_content_preserved(self) -> None:
        data = {
            "run_id": "unic-1",
            "status": "success",
            "events": [
                {"type": "message", "content": "héllo wörld — 日本語テキスト 🚀"},
                {"type": "tool", "name": "検索", "input": {"q": "数据"}},
            ],
        }
        trace = parse_trace_data(data)
        assert trace.events[0].input == "héllo wörld — 日本語テキスト 🚀"
        assert trace.events[1].name == "検索"

    def test_unicode_signatures_are_stable(self) -> None:
        data = {
            "run_id": "sig-1",
            "status": "failed",
            "events": [
                {"type": "tool", "name": "tööl", "input": {"q": "ünï"}, "error": "x"},
                {"type": "tool", "name": "tööl", "input": {"q": "ünï"}, "error": "x"},
                {"type": "tool", "name": "tööl", "input": {"q": "ünï"}, "error": "x"},
            ],
        }
        trace = parse_trace_data(data)
        sigs = {e.get_tool_signature() for e in trace.get_tool_calls()}
        assert len(sigs) == 1


class TestHugeInputs:
    def test_ten_thousand_events_parse_and_normalize(self) -> None:
        events = [
            {"type": "llm", "name": "gpt-4", "token_count": 10 + i, "output": f"out-{i}"}
            for i in range(10_000)
        ]
        data = {"run_id": "big-1", "status": "success", "events": events}
        t0 = time.perf_counter()
        trace = parse_trace_data(data)
        normalized = TraceNormalizer.normalize(trace)
        elapsed = time.perf_counter() - t0

        assert len(normalized.events) == 10_000
        assert normalized.stats.num_llm_calls == 10_000
        assert normalized.stats.total_tokens == sum(10 + i for i in range(10_000))
        # Loose sanity bound: parsing 10k events should not take minutes.
        assert elapsed < 30

    def test_deeply_nested_metadata_survives(self) -> None:
        deep = {}
        cursor = deep
        for i in range(200):
            cursor[f"k{i}"] = {}
            cursor = cursor[f"k{i}"]
        cursor["leaf"] = "value"
        trace = parse_trace_data(
            {
                "run_id": "deep-1",
                "status": "success",
                "events": [{"type": "message", "content": "hi", "metadata": deep}],
            }
        )
        assert trace.events[0].metadata is not None


class TestMissingAndWeirdFields:
    def test_empty_dict_parses_with_defaults(self) -> None:
        trace = parse_trace_data({})
        assert trace.run_id
        assert trace.status == TraceStatus.UNKNOWN
        assert trace.events == []

    def test_events_not_a_list_ignored(self) -> None:
        trace = parse_trace_data({"run_id": "x", "events": {"type": "message"}})
        assert trace.events == []

    def test_events_containing_strings_become_messages(self) -> None:
        trace = parse_trace_data(
            {"run_id": "x", "status": "success", "events": ["hello", "world", {"type": "tool", "name": "s"}]}
        )
        assert trace.events[0].type == EventType.MESSAGE
        assert trace.events[0].input == "hello"
        assert trace.events[2].type == EventType.TOOL_CALL

    def test_non_dict_metadata_does_not_crash(self) -> None:
        trace = parse_trace_data(
            {
                "run_id": "x",
                "status": "success",
                "events": [{"type": "message", "content": "hi", "metadata": "just-a-string"}],
            }
        )
        assert trace.events[0].metadata == {}

    def test_string_token_count_and_latency_coerced(self) -> None:
        trace = parse_trace_data(
            {
                "run_id": "x",
                "status": "success",
                "events": [{"type": "llm", "token_count": "42", "latency_ms": "12.7"}],
            }
        )
        assert trace.events[0].token_count == 42
        assert trace.events[0].latency_ms == 13

    def test_garbage_latency_does_not_crash(self) -> None:
        trace = parse_trace_data(
            {"run_id": "x", "status": "success", "events": [{"type": "llm", "latency_ms": "not-a-number"}]}
        )
        assert trace.events[0].latency_ms is None

    def test_zero_error_count_keeps_success_status(self) -> None:
        trace = parse_trace_data({"run_id": "x", "status": "success", "errors": 0})
        assert trace.status == TraceStatus.SUCCESS

    def test_error_list_flips_status(self) -> None:
        trace = parse_trace_data({"run_id": "x", "status": "success", "errors": ["boom"]})
        assert trace.status == TraceStatus.FAILED


class TestTimestampRobustness:
    def test_out_of_range_epoch_timestamp_does_not_crash(self) -> None:
        for bad in [1e30, -1e30, float("inf"), float("-inf"), "not-a-timestamp", 2 ** 63]:
            data = {"run_id": "x", "status": "success", "start_time": bad}
            trace = parse_trace_data(data)
            assert isinstance(trace.timestamp_start, datetime)

    def test_generic_millisecond_and_second_timestamps(self) -> None:
        seconds = 1_700_000_000
        ms = seconds * 1000
        for value in [seconds, ms]:
            trace = parse_trace_data({"run_id": "x", "start_time": value})
            assert trace.timestamp_start.timestamp() == pytest.approx(seconds)

    def test_iso_timestamps_with_and_without_offset(self) -> None:
        trace = parse_trace_data(
            {
                "run_id": "x",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-01T00:00:00+00:00",
            }
        )
        assert trace.timestamp_start is not None
        assert trace.timestamp_end is not None

    def test_mixed_naive_aware_timestamps_normalize_without_crash(self) -> None:
        """A LangGraph trace with aware ISO events but a naive fallback start
        time must normalize cleanly (no naive/aware comparison crashes)."""
        parser = LangGraphParser()
        data = {
            "run_id": "mixed-1",
            "status": "failed",
            "events": [
                {"type": "llm", "timestamp": "2024-01-01T00:00:00Z", "error": "x"},
                {"type": "tool", "name": "s", "timestamp": "2024-01-01T00:00:00+00:00", "error": "y"},
                {"type": "tool", "name": "s", "timestamp": "2024-01-01T00:00:00+00:00", "error": "z"},
                {"type": "tool", "name": "s", "timestamp": "2024-01-01T00:00:00+00:00", "error": "w"},
            ],
        }
        trace = parser.parse(data)
        normalized = TraceNormalizer.normalize(trace)
        assert TraceNormalizer.validate(normalized) == []
        assert all(e.timestamp is None or e.timestamp.tzinfo is not None for e in normalized.events)

    def test_otel_nanosecond_out_of_range(self) -> None:
        parser = OpenTelemetryParser()
        data = {
            "resourceSpans": [
                {
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "t1",
                                    "spanId": "s1",
                                    "name": "llm.chat",
                                    "startTimeUnixNano": 10 ** 40,
                                    "endTimeUnixNano": 10 ** 40,
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        trace = parser.parse(data)
        assert trace.timestamp_start is not None  # falls back to "now"


class TestFrameworkDetection:
    def test_otel_service_name_does_not_override_framework(self) -> None:
        parser = OpenTelemetryParser()
        data = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "my-agent-service"}},
                        ]
                    },
                    "scopeSpans": [{"spans": [{"traceId": "t1", "spanId": "s1", "name": "n"}]}],
                }
            ]
        }
        trace = parser.parse(data)
        assert trace.env.agent_framework == "opentelemetry"

    def test_otel_gen_ai_system_override(self) -> None:
        parser = OpenTelemetryParser()
        data = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "gen_ai.system", "value": {"stringValue": "anthropic"}},
                        ]
                    },
                    "scopeSpans": [{"spans": [{"traceId": "t1", "spanId": "s1", "name": "n"}]}],
                }
            ]
        }
        trace = parser.parse(data)
        assert trace.env.agent_framework == "anthropic"

    def test_langgraph_events_with_non_dict_entries(self) -> None:
        parser = LangGraphParser()
        data = {
            "run_id": "lg-1",
            "status": "failed",
            "events": ["a string event", None, 42, {"type": "error", "error": "boom"}],
        }
        trace = parser.parse(data)
        assert len(trace.events) == 1
        assert trace.events[0].type == EventType.ERROR

    def test_langgraph_runs_with_non_dict_runs(self) -> None:
        parser = LangGraphParser()
        data = {"run_id": "lg-2", "status": "success", "runs": ["oops", 7, {"events": [{"type": "llm"}]}]}
        trace = parser.parse(data)
        assert len(trace.events) == 1


class TestGenericParserDetails:
    def test_parent_id_renumbering(self) -> None:
        parser = GenericJSONParser()
        data = {
            "run_id": "p-1",
            "status": "success",
            "events": [
                {"type": "llm", "output": "think"},
                {"type": "tool", "name": "s", "parent_id": 0},
            ],
        }
        trace = parser.parse(data)
        normalized = TraceNormalizer.normalize(trace)
        assert [e.event_id for e in normalized.events] == [0, 1]
        assert normalized.events[1].parent_event_id == 0

    def test_missing_parent_id_cleared(self) -> None:
        parser = GenericJSONParser()
        data = {
            "run_id": "p-2",
            "status": "success",
            "events": [
                {"type": "message", "content": "a"},
                {"type": "tool", "name": "s", "parent_id": 999},
            ],
        }
        trace = parser.parse(data)
        normalized = TraceNormalizer.normalize(trace)
        assert normalized.events[1].parent_event_id is None

    def test_duplicate_event_ids_renumbered(self) -> None:
        parser = GenericJSONParser()
        data = {
            "run_id": "p-3",
            "status": "success",
            "events": [
                {"type": "message", "content": "a", "event_id": 5},
                {"type": "message", "content": "b", "event_id": 5},
            ],
        }
        trace = parser.parse(data)
        normalized = TraceNormalizer.normalize(trace)
        assert TraceNormalizer.validate(normalized) == []
        assert [e.event_id for e in normalized.events] == [0, 1]

    def test_start_end_events_merged(self) -> None:
        parser = GenericJSONParser()
        data = {
            "run_id": "merge-1",
            "status": "success",
            "events": [
                {"type": "llm_start", "name": "gpt-4", "input": "prompt", "ts": "2024-01-01T00:00:00Z"},
                {"type": "llm_end", "name": "gpt-4", "output": "answer", "latency_ms": 12.0},
            ],
        }
        trace = parser.parse(data)
        assert len(trace.events) == 1
        assert trace.events[0].type == EventType.LLM_CALL
        assert trace.events[0].output == "answer"

    def test_validate_reports_chronological_violations(self) -> None:
        parser = GenericJSONParser()
        data = {
            "run_id": "chrono-1",
            "status": "success",
            "events": [
                {"type": "message", "content": "first", "timestamp": "2024-01-02T00:00:00Z"},
                {"type": "message", "content": "second", "timestamp": "2024-01-01T00:00:00Z"},
            ],
        }
        trace = parser.parse(data)
        issues = TraceNormalizer.validate(trace)
        assert any("timestamp precedes" in issue for issue in issues)

    def test_normalize_is_idempotent(self) -> None:
        data = {
            "run_id": "idem-1",
            "status": "success",
            "events": [
                {"type": "llm", "output": "a"},
                {"type": "tool", "name": "s", "parent_id": 0},
            ],
        }
        trace = parse_trace_data(data)
        TraceNormalizer.normalize(trace)
        first = trace.model_dump()
        TraceNormalizer.normalize(trace)
        assert trace.model_dump() == first


class TestFixtureFormats:
    @pytest.mark.parametrize("filename", ["generic.json", "langgraph.json", "langchain.json", "opentelemetry.json"])
    def test_e2e_fixtures_parse(self, filename: str) -> None:
        trace = parse_trace_file(FIXTURES / filename)
        assert trace.run_id
        normalized = TraceNormalizer.normalize(trace)
        issues = TraceNormalizer.validate(normalized)
        # The generic fixture contains an intentional out-of-order timestamp;
        # the real contract is that issues are *reported*, not that traces are
        # pristine. Only hard structural problems (duplicate IDs, dangling
        # parents) are prohibited here.
        hard = [i for i in issues if "precedes" not in i]
        assert hard == []
