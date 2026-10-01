"""
Trace normalizer.

Provides utilities for normalizing and validating traces,
as well as calculating derived statistics.
"""

from datetime import timezone
from typing import Any

from agent_autopsy.schema import EventType, Trace, TraceStats


class TraceNormalizer:
    """Utilities for normalizing and enriching traces."""

    @staticmethod
    def normalize(trace: Trace) -> Trace:
        """
        Normalize a trace by:
        - Recalculating stats
        - Ensuring event IDs are sequential
        - Remapping parent_event_id references
        - Filling in missing timestamps
        - Validating chronological order
        """
        # Build old_id -> new_id mapping and renumber event IDs
        id_mapping = {}
        for i, event in enumerate(trace.events):
            old_id = event.event_id
            id_mapping[old_id] = i
            event.event_id = i

        # Remap parent_event_id values to maintain causal links
        for event in trace.events:
            if event.parent_event_id is not None:
                if event.parent_event_id in id_mapping:
                    event.parent_event_id = id_mapping[event.parent_event_id]
                else:
                    # Parent doesn't exist - set to None
                    event.parent_event_id = None

        # Infer missing timestamps
        TraceNormalizer._infer_missing_timestamps(trace)

        # Make timestamps comparable: parsers may mix timezone-aware ISO values
        # with naive fallbacks (e.g. datetime.now()); subtracting mixed
        # naive/aware datetimes raises TypeError deep inside detectors.
        TraceNormalizer._harmonize_timestamps(trace)

        # Recalculate stats
        trace.stats = TraceNormalizer.calculate_stats(trace)

        return trace

    @staticmethod
    def _harmonize_timestamps(trace: Trace) -> None:
        """Attach UTC to naive timestamps when any timestamp in the trace is aware."""
        timestamps = [trace.timestamp_start, trace.timestamp_end]
        timestamps.extend(e.timestamp for e in trace.events)
        any_aware = any(t is not None and t.tzinfo is not None for t in timestamps)
        if not any_aware:
            return
        for idx, ts in enumerate(timestamps):
            if ts is not None and ts.tzinfo is None:
                timestamps[idx] = ts.replace(tzinfo=timezone.utc)
        trace.timestamp_start, trace.timestamp_end = timestamps[0], timestamps[1]
        for event, ts in zip(trace.events, timestamps[2:]):
            event.timestamp = ts

    @staticmethod
    def _infer_missing_timestamps(trace: Trace) -> None:
        """Fill in missing timestamps based on adjacent events."""
        last_ts = trace.timestamp_start
        for event in trace.events:
            if event.timestamp is None:
                event.timestamp = last_ts
            else:
                last_ts = event.timestamp

    @staticmethod
    def _validate_chronological_order(trace: Trace) -> list[str]:
        """Check events are in chronological order and return issues."""
        issues = []
        last_ts = None
        for event in trace.events:
            if event.timestamp and last_ts:
                try:
                    out_of_order = event.timestamp < last_ts
                except TypeError:
                    # Mixed naive/aware timestamps: cannot compare reliably.
                    out_of_order = False
                if out_of_order:
                    issues.append(
                        f"Event {event.event_id} timestamp precedes previous event"
                    )
            if event.timestamp:
                last_ts = event.timestamp
        return issues

    @staticmethod
    def calculate_stats(trace: Trace) -> TraceStats:
        """Calculate statistics from trace events."""
        stats = TraceStats()

        for event in trace.events:
            if event.type == EventType.LLM_CALL:
                stats.num_llm_calls += 1
            elif event.type == EventType.TOOL_CALL:
                stats.num_tool_calls += 1

            if event.is_error():
                stats.num_errors += 1

            if event.token_count is not None:
                stats.total_tokens = (stats.total_tokens or 0) + event.token_count

            if event.latency_ms is not None:
                stats.total_latency_ms = (stats.total_latency_ms or 0) + event.latency_ms

        return stats

    @staticmethod
    def validate(trace: Trace) -> list[str]:
        """
        Validate trace structure and return list of issues.

        Returns:
            List of validation issue messages (empty if valid)
        """
        issues = []

        if not trace.run_id:
            issues.append("Missing run_id")

        if not trace.events:
            issues.append("No events in trace")

        # Check for duplicate event IDs
        event_ids = [e.event_id for e in trace.events]
        if len(event_ids) != len(set(event_ids)):
            issues.append("Duplicate event IDs detected")

        # Check parent references
        id_set = set(event_ids)
        for event in trace.events:
            if event.parent_event_id is not None and event.parent_event_id not in id_set:
                issues.append(f"Event {event.event_id} references non-existent parent {event.parent_event_id}")

        issues.extend(TraceNormalizer._validate_chronological_order(trace))

        return issues

    @staticmethod
    def get_summary(trace: Trace) -> dict[str, Any]:
        """Get a human-readable summary of the trace."""
        agent_ids = trace.get_agent_ids()
        return {
            "run_id": trace.run_id,
            "status": trace.status.value,
            "duration_ms": trace.stats.total_latency_ms,
            "total_events": len(trace.events),
            "llm_calls": trace.stats.num_llm_calls,
            "tool_calls": trace.stats.num_tool_calls,
            "errors": trace.stats.num_errors,
            "total_tokens": trace.stats.total_tokens,
            "has_task_context": trace.task is not None,
            "available_tools": len(trace.env.tools_available),
            "framework": trace.env.agent_framework,
            "model": trace.env.model,
            "agent_count": len(agent_ids),
            "agents": agent_ids,
        }
