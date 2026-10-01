"""
Benchmark and evaluation mode utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_autopsy.api import load_trace
from agent_autopsy.preanalysis import PatternDetector
from agent_autopsy.schema import Trace

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Aggregate benchmark metrics over multiple traces."""

    total_runs: int
    success_rate: float
    average_tokens: float
    average_latency_ms: float
    average_errors: float
    top_failure_patterns: list[dict[str, Any]]
    degradation_alerts: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs": self.total_runs,
            "success_rate": round(self.success_rate, 3),
            "average_tokens": round(self.average_tokens, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
            "average_errors": round(self.average_errors, 2),
            "top_failure_patterns": self.top_failure_patterns,
            "degradation_alerts": self.degradation_alerts,
        }


def benchmark_traces(trace_paths: list[str | Path]) -> BenchmarkResult:
    """Compute benchmark metrics for trace files."""
    traces = [_load_trace(path) for path in trace_paths]
    traces = [trace for trace in traces if trace is not None]
    if not traces:
        return BenchmarkResult(
            total_runs=0,
            success_rate=0.0,
            average_tokens=0.0,
            average_latency_ms=0.0,
            average_errors=0.0,
            top_failure_patterns=[],
            degradation_alerts=["No valid traces available for benchmark"],
        )

    successes = sum(1 for trace in traces if trace.status.value == "success")
    success_rate = successes / len(traces)
    avg_tokens = sum((trace.stats.total_tokens or 0) for trace in traces) / len(traces)
    avg_latency = sum((trace.stats.total_latency_ms or 0) for trace in traces) / len(traces)
    avg_errors = sum(trace.stats.num_errors for trace in traces) / len(traces)

    pattern_counts: dict[str, int] = {}
    for trace in traces:
        for pattern in PatternDetector(trace).detect_all():
            key = pattern.pattern_type.value
            pattern_counts[key] = pattern_counts.get(key, 0) + 1

    top_patterns = sorted(
        [{"pattern": key, "count": count} for key, count in pattern_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    degradation_alerts = _derive_degradation_alerts(traces, success_rate, avg_tokens, avg_latency)

    return BenchmarkResult(
        total_runs=len(traces),
        success_rate=success_rate,
        average_tokens=avg_tokens,
        average_latency_ms=avg_latency,
        average_errors=avg_errors,
        top_failure_patterns=top_patterns,
        degradation_alerts=degradation_alerts,
    )


def benchmark_trace_directory(
    directory: str | Path,
    limit: int = 100,
) -> BenchmarkResult:
    """Benchmark the newest trace files from a directory."""
    path = Path(directory).expanduser().resolve()
    files = sorted(path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, limit)]
    return benchmark_traces(files)


def _load_trace(path: str | Path) -> Trace | None:
    try:
        return load_trace(path)
    except Exception:
        logger.debug("Benchmark skipped unreadable trace %s", path, exc_info=True)
        return None


def _derive_degradation_alerts(
    traces: list[Trace],
    success_rate: float,
    avg_tokens: float,
    avg_latency: float,
) -> list[str]:
    alerts: list[str] = []
    if success_rate < 0.7:
        alerts.append(f"Success rate is low ({success_rate:.0%})")
    if avg_tokens > 100000:
        alerts.append(f"Average token usage is high ({avg_tokens:.0f})")
    if avg_latency > 120000:
        alerts.append(f"Average latency is high ({avg_latency:.0f} ms)")

    # Compare first half vs second half for simple trend alerts.
    ordered = sorted(
        traces,
        key=lambda t: (t.timestamp_start or datetime.min).timestamp(),
    )
    if len(ordered) >= 6:
        mid = len(ordered) // 2
        early = ordered[:mid]
        late = ordered[mid:]
        early_success = sum(1 for t in early if t.status.value == "success") / len(early)
        late_success = sum(1 for t in late if t.status.value == "success") / len(late)
        if early_success - late_success >= 0.2:
            alerts.append(
                f"Success rate dropped from {early_success:.0%} to {late_success:.0%} in recent runs"
            )
    return alerts
