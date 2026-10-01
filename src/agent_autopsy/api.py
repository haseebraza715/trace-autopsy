"""
Public facade for Agent Autopsy.

CLI, Streamlit, and MCP should depend on this module instead of reaching into
ingestion, preanalysis, and analysis internals.

LangChain/LangGraph are imported only inside :func:`run_llm_analysis` and
:func:`stream_llm_analysis_text` so deterministic paths avoid loading heavy deps.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_autopsy.analysis.agent import AnalysisResult, run_analysis_without_llm
from agent_autopsy.analysis.llm_cache import load_cached, save_cached
from agent_autopsy.errors import ParseError
from agent_autopsy.ingestion import TraceNormalizer, parse_trace_file
from agent_autopsy.ingestion.parser import parse_trace_data
from agent_autopsy.output import ReportGenerator
from agent_autopsy.preanalysis import PatternDetector, PatternResult, RootCauseBuilder
from agent_autopsy.preanalysis.suspects import PreAnalysisBundle
from agent_autopsy.schema import Trace
from agent_autopsy.utils.config import Config, get_config


def llm_credentials_configured(cfg: Config | None = None) -> bool:
    """Return True when the configured provider has credentials to call an LLM."""
    cfg = cfg or get_config()
    prov = (cfg.llm_provider or "openrouter").lower().strip()
    if prov == "openrouter":
        return bool(cfg.openrouter_api_key)
    if prov == "openai":
        return bool(cfg.openai_api_key or os.getenv("OPENAI_API_KEY", "").strip())
    if prov == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    if prov == "ollama":
        return True
    return bool(cfg.openrouter_api_key)


@dataclass
class Report:
    """Rendered autopsy report and pipeline outputs."""

    trace: Trace
    preanalysis: PreAnalysisBundle
    analysis: AnalysisResult
    report_generator: ReportGenerator

    def markdown(self) -> str:
        return self.report_generator.to_markdown()

    def json_dict(self) -> dict[str, Any]:
        return self.report_generator.to_json()


def apply_embedding_defaults_for_trace(trace: Trace) -> None:
    """Skip sentence embeddings when the trace has no task goal (saves memory)."""
    cfg = get_config()
    goal = ""
    if trace.task and trace.task.goal:
        goal = str(trace.task.goal).strip()
    if not goal:
        cfg.skip_embeddings = True


def load_trace(path: str | Path) -> Trace:
    """Parse and normalize a trace from disk."""
    trace = parse_trace_file(path)
    return _require_events(TraceNormalizer.normalize(trace))


def load_trace_from_dict(data: dict[str, Any]) -> Trace:
    """Parse and normalize a trace from an already-loaded JSON object."""
    trace = parse_trace_data(data)
    return _require_events(TraceNormalizer.normalize(trace))


def _require_events(trace: Trace) -> Trace:
    """Reject event-less traces: a run with no events cannot be analyzed,
    and reporting it as a healthy empty run would read as a clean pass."""
    if not trace.events:
        raise ParseError(f"Trace contains no events ({trace.run_id}); nothing to analyze")
    return trace


def run_preanalysis(trace: Trace) -> PreAnalysisBundle:
    """Deterministic root-cause and signal pass."""
    return RootCauseBuilder(trace).build()


def detect_patterns(trace: Trace) -> list[PatternResult]:
    """Run all deterministic pattern detectors."""
    return PatternDetector(trace).detect_all()


def generate_report(trace: Trace, analysis: AnalysisResult) -> ReportGenerator:
    """Build a report generator for markdown/JSON rendering."""
    return ReportGenerator(trace, analysis)


def run_llm_analysis(
    trace: Trace,
    *,
    model: str | None = None,
    verbose: bool = False,
    enable_tracing: bool | None = None,
    use_cache: bool = True,
) -> AnalysisResult:
    """LLM-assisted analysis (requires provider API keys)."""
    cfg = get_config()
    resolved_model = model or cfg.default_model

    if use_cache:
        cached = load_cached(trace, resolved_model)
        if cached is not None and cached.success and cached.report:
            return cached

    from agent_autopsy.analysis.llm_agent import run_analysis as _run_llm

    result = _run_llm(trace, model=model, verbose=verbose, enable_tracing=enable_tracing)
    if use_cache and result.success and result.report:
        save_cached(trace, resolved_model, result)
    return result


def stream_llm_analysis_text(
    trace: Trace,
    result_holder: dict[str, Any],
    *,
    model: str | None = None,
    verbose: bool = False,
    enable_tracing: bool | None = None,
    use_cache: bool = True,
) -> Iterator[str]:
    """
    Stream LLM / graph output as text chunks for UIs (e.g. ``st.write_stream``).

    When the iterator completes, ``result_holder['result']`` contains the final
    :class:`AnalysisResult` (unless the outer caller interrupted before completion).
    """
    if use_cache:
        cfg = get_config()
        cached = load_cached(trace, model or cfg.default_model)
        if cached is not None and cached.success and cached.report:
            result_holder["result"] = cached
            yield cached.report
            return

    from agent_autopsy.analysis.llm_agent import run_analysis_stream as _stream

    yield from _stream(
        trace,
        result_holder,
        model=model,
        verbose=verbose,
        enable_tracing=enable_tracing,
    )


def run_deterministic_analysis(trace: Trace) -> AnalysisResult:
    """Pre-analysis-only report without an LLM."""
    return run_analysis_without_llm(trace)


def analyze(
    trace_path: str | Path,
    *,
    no_llm: bool = False,
    no_embeddings: bool = False,
    model: str | None = None,
    verbose: bool = False,
    enable_tracing: bool | None = None,
    use_llm_cache: bool = True,
) -> Report:
    """
    Full pipeline: load trace, pre-analyze, optional LLM, wrap report generator.

    When ``no_embeddings`` is True, sentence-transformers are not loaded for drift.
    Traces without a task goal also skip embeddings automatically.
    """
    cfg = get_config()
    prev_skip = cfg.skip_embeddings
    try:
        cfg.skip_embeddings = prev_skip or no_embeddings
        trace = load_trace(trace_path)
        apply_embedding_defaults_for_trace(trace)
        preanalysis = run_preanalysis(trace)

        if no_llm or not llm_credentials_configured(cfg):
            analysis = run_deterministic_analysis(trace)
        else:
            analysis = run_llm_analysis(
                trace,
                model=model,
                verbose=verbose,
                enable_tracing=enable_tracing,
                use_cache=use_llm_cache,
            )

        gen = generate_report(trace, analysis)
        return Report(trace=trace, preanalysis=preanalysis, analysis=analysis, report_generator=gen)
    finally:
        cfg.skip_embeddings = prev_skip


def render_report(
    report: Report,
    format: Literal["markdown", "json", "text"] = "markdown",
) -> str:
    """Render a :class:`Report` to markdown, plain text, or JSON text."""
    if format == "json":
        import json

        return json.dumps(report.report_generator.to_json(), indent=2, default=str)
    return str(report.report_generator.render(format))


def trace_summary(trace: Trace) -> dict[str, Any]:
    """Lightweight stats dict for UI and MCP."""
    return TraceNormalizer.get_summary(trace)
