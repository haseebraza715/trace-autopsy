"""
Live trace monitoring with streaming alerts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from agent_autopsy.api import load_trace
from agent_autopsy.preanalysis import PatternDetector

logger = logging.getLogger(__name__)


@dataclass
class LiveAlert:
    """Alert emitted by live monitor."""

    trace_file: str
    run_id: str
    pattern_type: str
    severity: str
    message: str
    event_ids: list[int]


class LiveTraceMonitor:
    """
    Polling-based live trace monitor.

    Detects new/updated trace files and emits pattern alerts.
    """

    def __init__(
        self,
        trace_dir: str | Path = "./traces",
        poll_interval_seconds: float = 1.0,
    ):
        self.trace_dir = Path(trace_dir).expanduser().resolve()
        self.poll_interval_seconds = poll_interval_seconds
        self._last_seen_mtime: dict[str, float] = {}

    def run_once(self) -> list[LiveAlert]:
        """Process new/updated traces once and return alerts."""
        if not self.trace_dir.exists():
            return []
        alerts: list[LiveAlert] = []

        for trace_file in sorted(self.trace_dir.glob("*.json")):
            key = str(trace_file)
            try:
                mtime = trace_file.stat().st_mtime
            except OSError:
                # File vanished between glob and stat (writer moved it away).
                continue
            if key in self._last_seen_mtime and self._last_seen_mtime[key] >= mtime:
                continue

            try:
                trace = load_trace(trace_file)
            except Exception:
                logger.exception("LiveTraceMonitor failed to parse %s", trace_file)
                continue

            for pattern in PatternDetector(trace).detect_all():
                alerts.append(
                    LiveAlert(
                        trace_file=str(trace_file),
                        run_id=trace.run_id,
                        pattern_type=pattern.pattern_type.value,
                        severity=pattern.severity.value,
                        message=pattern.message,
                        event_ids=pattern.event_ids,
                    )
                )
            self._last_seen_mtime[key] = mtime
        return alerts

    def stream(self, duration_seconds: float | None = None) -> Iterator[LiveAlert]:
        """Yield alerts continuously for optional bounded duration."""
        start = time.time()
        while True:
            yield from self.run_once()
            if duration_seconds is not None and (time.time() - start) >= duration_seconds:
                return
            time.sleep(max(0.1, self.poll_interval_seconds))

    def run_with_callback(
        self,
        callback: Callable[[LiveAlert], None],
        duration_seconds: float | None = None,
    ) -> None:
        """Run monitor and invoke callback for each alert."""
        for alert in self.stream(duration_seconds=duration_seconds):
            callback(alert)
