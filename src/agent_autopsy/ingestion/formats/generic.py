"""
Generic JSON trace parser.

Fallback parser for trace formats that don't match specific parsers.
Attempts to extract common patterns from arbitrary JSON structures.
"""

import hashlib
from datetime import datetime
from typing import Any

from agent_autopsy.ingestion.parser import TraceParser
from agent_autopsy.schema import (
    EnvironmentInfo,
    EventError,
    EventRole,
    EventType,
    TaskContext,
    Trace,
    TraceEvent,
    TraceStatus,
)


class GenericJSONParser(TraceParser):
    """Fallback parser for generic JSON trace formats."""

    def can_parse(self, data: dict[str, Any]) -> bool:
        """Generic parser can always attempt to parse."""
        return isinstance(data, dict)

    def parse(self, data: dict[str, Any]) -> Trace:
        """Parse generic JSON into normalized format."""
        run_id = self._extract_run_id(data)
        timestamp_start, timestamp_end = self._extract_timestamps(data)
        status = self._extract_status(data)
        env = self._extract_environment(data)
        task = self._extract_task_context(data)
        events = self._extract_events(data)
        final_output = self._extract_final_output(data)
        error_summary = self._extract_error_summary(data, events)

        trace = Trace(
            run_id=run_id,
            trace_id=data.get("trace_id") or data.get("traceId"),
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            status=status,
            task=task,
            env=env,
            events=events,
            final_output=final_output,
            error_summary=error_summary,
        )

        trace.stats = trace.calculate_stats()
        return trace

    def _extract_run_id(self, data: dict[str, Any]) -> str:
        """Extract or generate run ID."""
        for key in ["run_id", "runId", "id", "execution_id", "session_id", "thread_id"]:
            if key in data:
                return str(data[key])
        return hashlib.md5(str(data).encode()).hexdigest()[:12]

    def _extract_timestamps(
        self, data: dict[str, Any]
    ) -> tuple[datetime, datetime | None]:
        """Extract timestamps with flexible field names."""
        start_keys = ["start_time", "startTime", "timestamp", "created_at", "createdAt", "started_at"]
        end_keys = ["end_time", "endTime", "finished_at", "finishedAt", "completed_at", "completedAt"]

        start = None
        end = None

        for key in start_keys:
            if key in data:
                start = self._parse_timestamp(data[key])
                if start:
                    break

        for key in end_keys:
            if key in data:
                end = self._parse_timestamp(data[key])
                if end:
                    break

        if start is None:
            start = datetime.now()

        return start, end

    def _parse_timestamp(self, value: Any) -> datetime | None:
        """Parse various timestamp formats; never raises on malformed values."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            # Handle both seconds and milliseconds
            try:
                if value > 1e12:  # milliseconds
                    return datetime.fromtimestamp(value / 1000)
                return datetime.fromtimestamp(value)
            except (OSError, ValueError, OverflowError):
                return None
        if isinstance(value, str):
            for fmt in [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ]:
                try:
                    return datetime.strptime(value.replace("+00:00", "Z"), fmt)
                except ValueError:
                    continue
        return None

    def _extract_status(self, data: dict[str, Any]) -> TraceStatus:
        """Extract status with flexible detection.

        Precedence: an actual error payload (string/dict/list) marks the run
        failed even when a conflicting ``status`` field says otherwise; a
        numeric count field like ``errors: 0`` is not an error payload.
        """
        for error_key in ["error", "exception"]:
            if data.get(error_key):
                return TraceStatus.FAILED
        if isinstance(data.get("errors"), list) and data["errors"]:
            return TraceStatus.FAILED

        for key in ["status", "state", "result"]:
            if key in data:
                status = str(data[key]).lower()
                if status in ["success", "completed", "done", "ok", "passed"]:
                    return TraceStatus.SUCCESS
                if status in ["failed", "error", "failure", "exception"]:
                    return TraceStatus.FAILED
                if status in ["timeout", "timed_out"]:
                    return TraceStatus.TIMEOUT
                if "loop" in status:
                    return TraceStatus.LOOP_DETECTED
                if status in ["cancelled", "canceled", "aborted", "interrupted"]:
                    return TraceStatus.CANCELLED

        return TraceStatus.SUCCESS

    def _extract_environment(self, data: dict[str, Any]) -> EnvironmentInfo:
        """Extract environment info from various structures."""
        tools = []
        context_window_tokens = None

        # Look for tools in common locations
        for key in ["tools", "functions", "available_tools", "tool_list"]:
            if key in data:
                tool_data = data[key]
                if isinstance(tool_data, list):
                    for t in tool_data:
                        if isinstance(t, str):
                            tools.append(t)
                        elif isinstance(t, dict):
                            tools.append(t.get("name", t.get("function", {}).get("name", str(t))))
                elif isinstance(tool_data, dict):
                    tools.extend(tool_data.keys())

        # Look for model
        model = None
        for key in ["model", "model_name", "modelName", "llm"]:
            if key in data:
                model = str(data[key])
                break

        # Try to detect framework
        framework = "unknown"
        data_str = str(data).lower()
        if "langgraph" in data_str:
            framework = "langgraph"
        elif "langchain" in data_str:
            framework = "langchain"
        elif "autogen" in data_str:
            framework = "autogen"
        elif "crewai" in data_str:
            framework = "crewai"

        # Per-trace context window override (if available)
        for key in ["context_window_tokens", "context_window", "context_limit", "max_context_tokens"]:
            raw = data.get(key)
            if isinstance(raw, (int, float, str)):
                try:
                    context_window_tokens = int(float(raw))
                    break
                except (TypeError, ValueError):
                    continue

        return EnvironmentInfo(
            agent_framework=framework,
            model=model,
            tools_available=sorted(set(tools)),
            context_window_tokens=context_window_tokens,
        )

    def _extract_task_context(self, data: dict[str, Any]) -> TaskContext | None:
        """Extract task context from common patterns."""
        goal = None

        for key in ["goal", "task", "query", "prompt", "input", "question", "objective"]:
            if key in data:
                value = data[key]
                if isinstance(value, str):
                    goal = value
                elif isinstance(value, dict):
                    goal = value.get("text") or value.get("content") or value.get("goal")
                break

        if not goal:
            return None

        return TaskContext(
            goal=goal,
            success_criteria=data.get("success_criteria") or data.get("criteria"),
            expected_output_type=data.get("expected_output_type") or data.get("output_type"),
        )

    def _merge_start_end_events(self, raw_events: list[dict]) -> list[dict]:
        """
        Merge paired *_start and *_end events into single events.

        TraceSaver emits separate start/end events (e.g., llm_start, llm_end).
        This method pairs them to avoid double-counting in pattern detection.
        """
        pending_starts: dict[tuple, dict] = {}  # key: (base_type, name, run_id) -> event_data
        merged_events = []

        for raw in raw_events:
            if not isinstance(raw, dict):
                merged_events.append(raw)
                continue

            type_value = str(raw.get("type", "")).lower()
            name = raw.get("name", "")
            run_id = raw.get("run_id") or raw.get("event_id")

            if type_value.endswith("_start"):
                # Store start event for later merging
                base_type = type_value.replace("_start", "")
                key = (base_type, name, run_id)
                pending_starts[key] = raw.copy()
                continue

            if type_value.endswith("_end"):
                # Find matching start and merge
                base_type = type_value.replace("_end", "")
                key = (base_type, name, run_id)
                start_event = pending_starts.pop(key, None)

                if start_event:
                    # Merge: use start's input, end's output/tokens/latency
                    merged = {
                        **start_event,
                        "type": base_type,
                        "output": raw.get("output") or start_event.get("output"),
                        "latency_ms": raw.get("latency_ms") or start_event.get("latency_ms"),
                        "token_count": raw.get("token_count") or start_event.get("token_count"),
                        "tokens": raw.get("tokens") or start_event.get("tokens"),
                        "error": raw.get("error") or start_event.get("error"),
                    }
                    # Preserve the end timestamp as the event timestamp
                    if raw.get("ts") or raw.get("timestamp"):
                        merged["timestamp"] = raw.get("timestamp") or raw.get("ts")
                        merged["ts"] = raw.get("ts") or raw.get("timestamp")
                    merged_events.append(merged)
                else:
                    # No matching start, use end event as-is with base type
                    event_copy = raw.copy()
                    event_copy["type"] = base_type
                    merged_events.append(event_copy)
                continue

            # Non-start/end events pass through unchanged
            merged_events.append(raw)

        # Add any unmatched start events (shouldn't happen normally)
        for raw in pending_starts.values():
            raw_copy = raw.copy()
            raw_copy["type"] = raw_copy["type"].replace("_start", "")
            merged_events.append(raw_copy)

        return merged_events

    def _extract_events(self, data: dict[str, Any]) -> list[TraceEvent]:
        """Extract events from various structures."""
        events = []
        event_id = 0

        # Look for events in common locations
        raw_events = []
        for key in ["events", "steps", "messages", "logs", "history", "trace", "actions"]:
            if key in data and isinstance(data[key], list):
                raw_events = data[key]
                break

        # Merge paired start/end events to avoid double-counting
        raw_events = self._merge_start_end_events(raw_events)

        for raw in raw_events:
            if not isinstance(raw, dict):
                # Handle string messages
                events.append(
                    TraceEvent(
                        event_id=event_id,
                        type=EventType.MESSAGE,
                        input=str(raw),
                    )
                )
                event_id += 1
                continue

            event_type = self._determine_event_type(raw)
            role = self._determine_role(raw)

            # Extract error if present
            error = None
            for error_key in ["error", "exception", "failure"]:
                if error_key in raw:
                    error_data = raw[error_key]
                    if isinstance(error_data, str):
                        error = EventError(message=error_data)
                    elif isinstance(error_data, dict):
                        error = EventError(
                            message=error_data.get("message", str(error_data)),
                            stack=error_data.get("stack") or error_data.get("traceback"),
                            category=error_data.get("type") or error_data.get("category"),
                        )
                    break

            raw_metadata = raw.get("metadata")
            if not isinstance(raw_metadata, dict):
                raw_metadata = {}

            # ``or`` chains would treat a valid parent id of 0 as missing.
            raw_parent = raw.get("parent_id")
            if raw_parent is None:
                raw_parent = raw.get("parentId")

            event = TraceEvent(
                event_id=event_id,
                parent_event_id=self._safe_parent_event_id(raw_parent),
                span_id=raw.get("span_id") or raw.get("spanId"),
                agent_id=(
                    raw.get("agent_id")
                    or raw.get("agentId")
                    or raw.get("agent")
                    or raw.get("sender")
                    or raw.get("participant")
                ),
                type=event_type,
                role=role,
                name=raw.get("name") or raw.get("tool") or raw.get("function") or raw.get("node"),
                input=raw.get("input") or raw.get("args") or raw.get("content") or raw.get("query"),
                output=raw.get("output") or raw.get("result") or raw.get("response"),
                token_count=raw.get("token_count") or raw.get("tokens") or raw.get("tokenCount"),
                latency_ms=raw.get("latency_ms") or raw.get("duration_ms") or raw.get("latency"),
                timestamp=self._parse_timestamp(raw.get("timestamp") or raw.get("time") or raw.get("ts")),
                error=error,
                metadata=raw_metadata,
            )
            events.append(event)
            event_id += 1

        return events

    def _safe_parent_event_id(self, value: Any) -> int | None:
        """Convert parent IDs to int when possible."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _determine_event_type(self, raw: dict[str, Any]) -> EventType:
        """Determine event type from raw data."""
        type_value = str(raw.get("type", "")).lower()

        type_mapping = {
            "llm": EventType.LLM_CALL,
            "model": EventType.LLM_CALL,
            "chat": EventType.LLM_CALL,
            "completion": EventType.LLM_CALL,
            "tool": EventType.TOOL_CALL,
            "function": EventType.TOOL_CALL,
            "action": EventType.TOOL_CALL,
            "decision": EventType.DECISION,
            "router": EventType.DECISION,
            "branch": EventType.DECISION,
            "error": EventType.ERROR,
            "exception": EventType.ERROR,
            "message": EventType.MESSAGE,
        }

        for key, event_type in type_mapping.items():
            if key in type_value:
                return event_type

        # Infer from content
        if any(k in raw for k in ["tool", "function", "action"]):
            return EventType.TOOL_CALL
        if any(k in raw for k in ["error", "exception"]):
            return EventType.ERROR
        if "model" in raw:
            return EventType.LLM_CALL

        return EventType.MESSAGE

    def _determine_role(self, raw: dict[str, Any]) -> EventRole | None:
        """Determine role from raw data."""
        role = str(raw.get("role", "")).lower()

        role_mapping = {
            "system": EventRole.SYSTEM,
            "user": EventRole.USER,
            "human": EventRole.USER,
            "assistant": EventRole.ASSISTANT,
            "ai": EventRole.ASSISTANT,
            "bot": EventRole.ASSISTANT,
            "tool": EventRole.TOOL,
            "function": EventRole.TOOL,
        }

        return role_mapping.get(role)

    def _extract_final_output(self, data: dict[str, Any]) -> str | dict | None:
        """Extract final output."""
        for key in ["output", "result", "response", "answer", "final_output"]:
            if key in data:
                return data[key]
        return None

    def _extract_error_summary(
        self, data: dict[str, Any], events: list[TraceEvent]
    ) -> str | None:
        """Extract error summary."""
        for key in ["error", "error_message", "exception"]:
            if key in data:
                error = data[key]
                if isinstance(error, str):
                    return error
                if isinstance(error, dict):
                    return error.get("message", str(error))

        # Collect from events
        errors = [e.error.message for e in events if e.error]
        if errors:
            return "; ".join(errors[:3])

        return None
