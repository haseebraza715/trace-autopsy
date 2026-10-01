"""Disk cache for LLM analysis results (trace digest + model + prompt version)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_autopsy.analysis.agent import AnalysisResult
from agent_autopsy.schema import Trace
from agent_autopsy.utils.atomic import atomic_write_json

PROMPT_VERSION = "autopsy-llm-v1"


def cache_dir() -> Path:
    base = Path.home() / ".cache" / "agent-autopsy"
    base.mkdir(parents=True, exist_ok=True)
    return base


def trace_digest(trace: Trace) -> str:
    """Stable hash of normalized trace content."""
    try:
        raw = trace.model_dump_json()
    except Exception:
        raw = json.dumps(trace.model_dump(), default=str, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_key(trace: Trace, model: str, *, prompt_version: str = PROMPT_VERSION) -> str:
    key_material = f"{trace_digest(trace)}|{model}|{prompt_version}"
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def cache_path(trace: Trace, model: str) -> Path:
    return cache_dir() / f"analysis-{cache_key(trace, model)}.json"


def load_cached(trace: Trace, model: str) -> AnalysisResult | None:
    path = cache_path(trace, model)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        return AnalysisResult(
            report=data["report"],
            trace_summary=data["trace_summary"],
            preanalysis=data["preanalysis"],
            success=data.get("success", True),
            error=data.get("error"),
        )
    except Exception:
        return None


def save_cached(trace: Trace, model: str, result: AnalysisResult) -> None:
    path = cache_path(trace, model)
    payload: dict[str, Any] = {
        "report": result.report,
        "trace_summary": result.trace_summary,
        "preanalysis": result.preanalysis,
        "success": result.success,
        "error": result.error,
    }
    atomic_write_json(path, payload)
