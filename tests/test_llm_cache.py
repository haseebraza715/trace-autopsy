"""Disk-cache behavior for LLM analysis results (isolated in tmp dirs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_autopsy.analysis import llm_cache
from agent_autopsy.analysis.agent import AnalysisResult
from agent_autopsy.ingestion import parse_trace_data
from agent_autopsy.ingestion.normalizer import TraceNormalizer


@pytest.fixture()
def trace():
    data = {
        "run_id": "cache-run",
        "status": "failed",
        "events": [
            {"type": "tool", "name": "search", "input": {"q": "x"}, "error": "boom"},
            {"type": "tool", "name": "search", "input": {"q": "x"}, "error": "boom"},
            {"type": "tool", "name": "search", "input": {"q": "x"}, "error": "boom"},
        ],
    }
    return TraceNormalizer.normalize(parse_trace_data(data))


@pytest.fixture()
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr(llm_cache, "cache_dir", lambda: root)
    return root


def _result(report: str = "## Summary\nok") -> AnalysisResult:
    return AnalysisResult(
        report=report,
        trace_summary={"run_id": "cache-run"},
        preanalysis={"signals": []},
        success=True,
    )


class TestDigest:
    def test_digest_changes_when_content_changes(self, trace) -> None:
        before = llm_cache.trace_digest(trace)
        trace.events[0].input = {"q": "DIFFERENT"}
        after = llm_cache.trace_digest(trace)
        assert before != after

    def test_digest_deterministic(self, trace) -> None:
        assert llm_cache.trace_digest(trace) == llm_cache.trace_digest(trace)


class TestRoundTrip:
    def test_save_then_load_returns_equivalent_result(self, trace, isolated_cache: Path) -> None:
        assert not list(isolated_cache.iterdir())
        llm_cache.save_cached(trace, "model-a", _result(report="hello world"))
        files = list(isolated_cache.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".json"

        loaded = llm_cache.load_cached(trace, "model-a")
        assert loaded is not None
        assert loaded.report == "hello world"
        assert loaded.success is True

    def test_load_miss_returns_none(self, trace, isolated_cache: Path) -> None:
        assert llm_cache.load_cached(trace, "never-saved") is None

    def test_different_model_is_a_miss(self, trace, isolated_cache: Path) -> None:
        llm_cache.save_cached(trace, "model-a", _result())
        assert llm_cache.load_cached(trace, "model-b") is None

    def test_corrupted_cache_file_returns_none(self, trace, isolated_cache: Path) -> None:
        (isolated_cache / "analysis-xxx.json").write_text("{not valid json", encoding="utf-8")
        # path won't match the real digest, so write one directly:
        path = llm_cache.cache_path(trace, "model-a")
        path.write_text("{corrupted", encoding="utf-8")
        assert llm_cache.load_cached(trace, "model-a") is None

    def test_failed_result_is_not_cached_as_success(self, trace, isolated_cache: Path) -> None:
        failed = AnalysisResult(
            report="",
            trace_summary={},
            preanalysis={},
            success=False,
            error="provider down",
        )
        llm_cache.save_cached(trace, "model-a", failed)
        loaded = llm_cache.load_cached(trace, "model-a")
        assert loaded is not None and loaded.success is False
        assert loaded.error == "provider down"

    def test_write_is_atomic_no_tmp_leftovers(self, trace, isolated_cache: Path) -> None:
        llm_cache.save_cached(trace, "model-a", _result())
        leftovers = [p for p in isolated_cache.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []


class TestPayload:
    def test_payload_has_all_fields(self, trace, isolated_cache: Path) -> None:
        llm_cache.save_cached(trace, "model-a", _result())
        path = llm_cache.cache_path(trace, "model-a")
        data = json.loads(path.read_text())
        for key in ["report", "trace_summary", "preanalysis", "success", "error"]:
            assert key in data


class TestStreamCacheBypass:
    def test_stream_yields_cached_report_without_network(self, trace, monkeypatch):
        """Streaming must serve warm cache entries without touching the wire."""
        from agent_autopsy import api
        from agent_autopsy.analysis.agent import AnalysisResult

        def explode(*a, **k):
            raise AssertionError("network stream invoked despite warm cache")

        monkeypatch.setattr(
            "agent_autopsy.analysis.llm_agent.run_analysis_stream", explode
        )
        result = AnalysisResult(
            report="# cached narrative",
            trace_summary={},
            preanalysis={"signals": []},
            success=True,
        )
        llm_cache.save_cached(trace, "test-model", result)

        holder: dict = {}
        chunks = list(
            api.stream_llm_analysis_text(trace, holder, model="test-model")
        )
        assert chunks == ["# cached narrative"]
        # load_cached round-trips through JSON, so compare by value.
        assert holder["result"].report == "# cached narrative"
        assert holder["result"].success
