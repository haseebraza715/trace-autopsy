"""
LangGraph trace parser.

Parses LangGraph execution traces into the normalized TraceSchemaV2 format.
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


class LangGraphParser(TraceParser):
    """Parser for LangGraph trace format."""

    def can_parse(self, data: dict[str, Any]) -> bool:
        """Check if this is a LangGraph trace."""
        # Check for common LangGraph trace indicators
        if "thread_id" in data:
            return True
        if "runs" in data and isinstance(data.get("runs"), list):
            return True
        if "checkpoint" in data:
            return True
        if "events" in data and any(
            "langgraph" in str(e).lower() for e in data.get("events", [])
        ):
            return True
        # Check for node-based structure
        if "nodes" in data or "edges" in data:
            return True
        return False

    def parse(self, data: dict[str, Any]) -> Trace:
        """Parse LangGraph trace into normalized format."""
        # Extract run ID
        run_id = self._extract_run_id(data)

        # Extract timestamps
        timestamp_start, timestamp_end = self._extract_timestamps(data)

        # Extract status
        status = self._extract_status(data)

        # Extract environment info
        env = self._extract_environment(data)

        # Extract task context
        task = self._extract_task_context(data)

        # Extract and normalize events
        events = self._extract_events(data)

        # Extract final output and error summary
        final_output = self._extract_final_output(data)
        error_summary = self._extract_error_summary(data, events)

        # Build trace
        trace = Trace(
            run_id=run_id,
            trace_id=data.get("trace_id"),
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            status=status,
            task=task,
            env=env,
            events=events,
            final_output=final_output,
            error_summary=error_summary,
        )

        # Calculate stats
        trace.stats = trace.calculate_stats()

        return trace

    def _extract_run_id(self, data: dict[str, Any]) -> str:
        """Extract or generate run ID."""
        if "run_id" in data:
            return str(data["run_id"])
        if "thread_id" in data:
            return str(data["thread_id"])
        if "id" in data:
            return str(data["id"])
        # Generate from content hash
        return hashlib.md5(str(data).encode()).hexdigest()[:12]

    def _extract_timestamps(
        self, data: dict[str, Any]
    ) -> tuple[datetime, datetime | None]:
        """Extract start and end timestamps."""
        start = None
        end = None

        # Try common timestamp fields
        for field in ["start_time", "timestamp", "created_at", "started_at"]:
            if field in data:
                start = self._parse_timestamp(data[field])
                break

        for field in ["end_time", "finished_at", "completed_at"]:
            if field in data:
                end = self._parse_timestamp(data[field])
                break

        # Try to get from events
        if start is None and "events" in data and data["events"]:
            first_event = data["events"][0]
            if "timestamp" in first_event:
                start = self._parse_timestamp(first_event["timestamp"])

        if end is None and "events" in data and data["events"]:
            last_event = data["events"][-1]
            if "timestamp" in last_event:
                end = self._parse_timestamp(last_event["timestamp"])

        # Default to now if no timestamp found
        if start is None:
            start = datetime.now()

        return start, end

    def _parse_timestamp(self, value: Any) -> datetime | None:
        """Parse a timestamp value into datetime; never raises on bad values."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            # Assume Unix timestamp (seconds or milliseconds)
            try:
                if value > 1e12:
                    return datetime.fromtimestamp(value / 1000)
                return datetime.fromtimestamp(value)
            except (OSError, ValueError, OverflowError):
                return None
        if isinstance(value, str):
            # Try ISO format
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return None

    def _extract_status(self, data: dict[str, Any]) -> TraceStatus:
        """Extract trace status."""
        status = (data.get("status") or "").lower()

        if status in ["success", "completed", "done"]:
            return TraceStatus.SUCCESS
        if status in ["failed", "error", "failure"]:
            return TraceStatus.FAILED
        if status in ["timeout", "timed_out"]:
            return TraceStatus.TIMEOUT
        if status in ["loop", "loop_detected", "infinite_loop"]:
            return TraceStatus.LOOP_DETECTED
        if status in ["cancelled", "canceled", "interrupted"]:
            return TraceStatus.CANCELLED

        # Infer from error presence
        if data.get("error") or data.get("exception"):
            return TraceStatus.FAILED

        # Check events for errors (ignore non-dict entries)
        events = data.get("events", [])
        if any(isinstance(e, dict) and (e.get("type") == "error" or e.get("error")) for e in events):
            return TraceStatus.FAILED

        return TraceStatus.SUCCESS

    def _extract_environment(self, data: dict[str, Any]) -> EnvironmentInfo:
        """Extract environment information."""
        tools_available = []
        context_window_tokens = None

        # Extract tools from various locations
        if "tools" in data:
            tools = data["tools"]
            if isinstance(tools, list):
                tools_available = [
                    t.get("name", str(t)) if isinstance(t, dict) else str(t)
                    for t in tools
                ]
            elif isinstance(tools, dict):
                tools_available = list(tools.keys())

        # Try to get from config
        config = data.get("config") or {}
        if "tools" in config:
            tools = config["tools"]
            if isinstance(tools, list):
                tools_available.extend(
                    t.get("name", str(t)) if isinstance(t, dict) else str(t)
                    for t in tools
                )

        # Extract model
        model = data.get("model") or config.get("model")

        for key in ["context_window_tokens", "context_window", "context_limit", "max_context_tokens"]:
            raw = data.get(key, config.get(key))
            if isinstance(raw, (int, float, str)):
                try:
                    context_window_tokens = int(float(raw))
                    break
                except (TypeError, ValueError):
                    continue

        return EnvironmentInfo(
            agent_framework="langgraph",
            model=model,
            tools_available=sorted(set(tools_available)),
            context_window_tokens=context_window_tokens,
        )

    def _extract_task_context(self, data: dict[str, Any]) -> TaskContext | None:
        """Extract task context for drift analysis."""
        task_data = data.get("task") or {}
        input_data = data.get("input") or {}

        goal = task_data.get("goal") or input_data.get("goal") or input_data.get("query")

        # If input is a string, use it as goal
        if goal is None and isinstance(data.get("input"), str):
            goal = data["input"]

        if not goal:
            return None

        return TaskContext(
            goal=goal,
            success_criteria=task_data.get("success_criteria"),
            expected_output_type=task_data.get("expected_output_type"),
        )

    def _extract_events(self, data: dict[str, Any]) -> list[TraceEvent]:
        """Extract and normalize events."""
        events = []
        event_id = 0

        # Get raw events from various locations
        raw_events = data.get("events") or []
        if not isinstance(raw_events, list):
            raw_events = [raw_events] if isinstance(raw_events, dict) else []
        if not raw_events and "runs" in data:
            # Handle runs-based format
            for run in data["runs"]:
                if isinstance(run, dict):
                    raw_events.extend(run.get("events", []))
                else:
                    raw_events.append(run)

        if not raw_events and "steps" in data:
            raw_events = data["steps"] if isinstance(data["steps"], list) else []

        if not raw_events and "messages" in data:
            raw_events = data["messages"] if isinstance(data["messages"], list) else []

        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                continue
            parsed_events = self._parse_event(raw_event, event_id)
            events.extend(parsed_events)
            event_id += len(parsed_events)

        return events

    def _parse_event(
        self, raw: dict[str, Any], start_id: int
    ) -> list[TraceEvent]:
        """Parse a raw event into one or more TraceEvents."""
        events = []

        # Determine event type
        event_type = self._determine_event_type(raw)
        role = self._determine_role(raw)

        # Extract common fields
        name = raw.get("name") or raw.get("node") or raw.get("tool")
        input_data = raw.get("input") or raw.get("args") or raw.get("content")
        output_data = raw.get("output") or raw.get("result") or raw.get("response")

        # Handle error
        error = None
        if "error" in raw:
            error_data = raw["error"]
            if isinstance(error_data, str):
                error = EventError(message=error_data)
            elif isinstance(error_data, dict):
                error = EventError(
                    message=error_data.get("message", str(error_data)),
                    stack=error_data.get("stack") or error_data.get("traceback"),
                    category=error_data.get("type") or error_data.get("category"),
                )

        event = TraceEvent(
            event_id=start_id,
            parent_event_id=self._safe_parent_event_id(raw.get("parent_id")),
            span_id=raw.get("span_id"),
            agent_id=(
                raw.get("agent_id")
                or raw.get("agent")
                or raw.get("node")
                or raw.get("sender")
            ),
            type=event_type,
            role=role,
            name=name,
            input=input_data,
            output=output_data,
            token_count=raw.get("token_count") or raw.get("tokens"),
            latency_ms=raw.get("latency_ms") or raw.get("duration_ms"),
            timestamp=self._parse_timestamp(raw.get("timestamp")),
            error=error,
            metadata=raw.get("metadata", {}),
        )

        events.append(event)

        # Handle nested events (e.g., tool results within LLM calls)
        if "tool_calls" in raw:
            for i, tool_call in enumerate(raw["tool_calls"]):
                nested_event = TraceEvent(
                    event_id=start_id + 1 + i,
                    parent_event_id=start_id,
                    agent_id=raw.get("agent_id") or raw.get("agent") or raw.get("node"),
                    type=EventType.TOOL_CALL,
                    role=EventRole.TOOL,
                    name=tool_call.get("name") or tool_call.get("function", {}).get("name"),
                    input=tool_call.get("args") or tool_call.get("function", {}).get("arguments"),
                    output=tool_call.get("result"),
                    metadata=tool_call.get("metadata", {}),
                )
                events.append(nested_event)

        return events

    def _determine_event_type(self, raw: dict[str, Any]) -> EventType:
        """Determine the event type from raw data."""
        explicit_type = (raw.get("type") or "").lower()

        if explicit_type in ["llm", "llm_call", "model", "chat"]:
            return EventType.LLM_CALL
        if explicit_type in ["tool", "tool_call", "function", "function_call"]:
            return EventType.TOOL_CALL
        if explicit_type in ["decision", "router", "branch", "condition"]:
            return EventType.DECISION
        if explicit_type in ["error", "exception", "failure"]:
            return EventType.ERROR
        if explicit_type in ["message", "msg"]:
            return EventType.MESSAGE

        # Infer from content
        if "error" in raw or "exception" in raw:
            return EventType.ERROR
        if "tool" in raw or "function" in raw:
            return EventType.TOOL_CALL
        if "model" in raw or "llm" in (raw.get("name") or "").lower():
            return EventType.LLM_CALL
        if raw.get("role") in ["user", "assistant", "system"]:
            return EventType.MESSAGE

        return EventType.MESSAGE

    def _determine_role(self, raw: dict[str, Any]) -> EventRole | None:
        """Determine the role from raw data."""
        role = (raw.get("role") or "").lower()

        if role == "system":
            return EventRole.SYSTEM
        if role == "user":
            return EventRole.USER
        if role == "assistant":
            return EventRole.ASSISTANT
        if role in ["tool", "function"]:
            return EventRole.TOOL

        # Infer from event type
        if raw.get("type") in ["tool", "tool_call", "function"]:
            return EventRole.TOOL

        return None

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

    def _extract_final_output(self, data: dict[str, Any]) -> str | dict | None:
        """Extract the final output from the trace."""
        if "output" in data:
            return data["output"]
        if "result" in data:
            return data["result"]
        if "response" in data:
            return data["response"]

        # Try to get from last event
        events = data.get("events", [])
        if events:
            last = events[-1]
            return last.get("output") or last.get("result") or last.get("content")

        return None

    def _extract_error_summary(
        self, data: dict[str, Any], events: list[TraceEvent]
    ) -> str | None:
        """Extract error summary from trace."""
        if "error" in data:
            error = data["error"]
            if isinstance(error, str):
                return error
            if isinstance(error, dict):
                return error.get("message", str(error))

        # Collect errors from events
        error_messages = []
        for event in events:
            if event.error:
                error_messages.append(event.error.message)

        if error_messages:
            return "; ".join(error_messages[:3])  # Limit to first 3

        return None
