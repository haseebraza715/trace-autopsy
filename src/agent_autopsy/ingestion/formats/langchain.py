"""
LangChain trace parser.

Parses LangChain execution traces into the normalized TraceSchemaV2 format.
Handles LangChain callback events, run traces, and agent executions.
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


class LangChainParser(TraceParser):
    """Parser for LangChain trace format."""

    def can_parse(self, data: dict[str, Any]) -> bool:
        """Check if this is a LangChain trace."""
        # Check for LangChain-specific fields
        if "run_type" in data and data.get("run_type") in ["chain", "llm", "tool", "agent"]:
            return True
        if "callbacks" in data:
            return True
        if "lc_namespace" in data or "lc" in data:
            return True
        # Check for LangSmith/LangChain run structure
        if "parent_run_id" in data or "child_runs" in data:
            return True
        # Check for runs with langchain metadata
        if "runs" in data and isinstance(data["runs"], list):
            for run in data["runs"]:
                if isinstance(run, dict) and run.get("run_type"):
                    return True
        return False

    def parse(self, data: dict[str, Any]) -> Trace:
        """Parse LangChain trace into normalized format."""
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
            trace_id=data.get("trace_id") or data.get("id"),
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
        for key in ["run_id", "id", "session_id", "trace_id"]:
            if key in data:
                return str(data[key])
        # Generate from content hash
        return hashlib.md5(str(data).encode()).hexdigest()[:12]

    def _extract_timestamps(
        self, data: dict[str, Any]
    ) -> tuple[datetime, datetime | None]:
        """Extract start and end timestamps."""
        start = None
        end = None

        # Try common timestamp fields
        for field in ["start_time", "startTime", "timestamp", "created_at"]:
            if field in data:
                start = self._parse_timestamp(data[field])
                if start:
                    break

        for field in ["end_time", "endTime", "finished_at", "completed_at"]:
            if field in data:
                end = self._parse_timestamp(data[field])
                if end:
                    break

        # Try to get from runs
        if start is None and "runs" in data and data["runs"]:
            first_run = data["runs"][0]
            if isinstance(first_run, dict) and "start_time" in first_run:
                start = self._parse_timestamp(first_run["start_time"])

        if end is None and "runs" in data and data["runs"]:
            last_run = data["runs"][-1]
            if isinstance(last_run, dict) and "end_time" in last_run:
                end = self._parse_timestamp(last_run["end_time"])

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
            # Handle both seconds and milliseconds
            try:
                if value > 1e12:
                    return datetime.fromtimestamp(value / 1000)
                return datetime.fromtimestamp(value)
            except (OSError, ValueError, OverflowError):
                return None
        if isinstance(value, str):
            # Try ISO format
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
            # Try fromisoformat
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return None

    def _extract_status(self, data: dict[str, Any]) -> TraceStatus:
        """Extract trace status."""
        status = str(data.get("status", "") or data.get("error", "")).lower()

        if status in ["success", "completed", "done", "ok"]:
            return TraceStatus.SUCCESS
        if status in ["failed", "error", "failure"]:
            return TraceStatus.FAILED
        if status in ["timeout", "timed_out"]:
            return TraceStatus.TIMEOUT

        # Check for error indicators
        if data.get("error") or data.get("exception"):
            return TraceStatus.FAILED

        # Check runs for errors
        if "runs" in data:
            for run in data.get("runs", []):
                if isinstance(run, dict) and run.get("error"):
                    return TraceStatus.FAILED

        return TraceStatus.SUCCESS

    def _extract_environment(self, data: dict[str, Any]) -> EnvironmentInfo:
        """Extract environment information."""
        tools_available = []
        context_window_tokens = None

        # Extract tools from various locations
        for key in ["tools", "functions", "available_tools"]:
            if key in data:
                tools = data[key]
                if isinstance(tools, list):
                    for t in tools:
                        if isinstance(t, str):
                            tools_available.append(t)
                        elif isinstance(t, dict):
                            name = t.get("name") or t.get("function", {}).get("name")
                            if name:
                                tools_available.append(name)

        # Extract model from serialized or extra data
        model = None
        for key in ["model", "model_name", "llm"]:
            if key in data:
                model_val = data[key]
                if isinstance(model_val, str):
                    model = model_val
                elif isinstance(model_val, dict):
                    model = model_val.get("model_name") or model_val.get("model")
                break

        # Try to get from extra
        if model is None and "extra" in data:
            extra = data["extra"]
            if isinstance(extra, dict):
                invocation = extra.get("invocation_params", {})
                model = invocation.get("model_name") or invocation.get("model")

                for key in ["context_window_tokens", "context_window", "max_context_tokens"]:
                    raw = invocation.get(key) or extra.get(key)
                    if isinstance(raw, (int, float, str)):
                        try:
                            context_window_tokens = int(float(raw))
                            break
                        except (TypeError, ValueError):
                            continue

        # Try to extract from runs metadata
        if "runs" in data and isinstance(data["runs"], list):
            for run in data["runs"]:
                if not isinstance(run, dict):
                    continue
                run_type = str(run.get("run_type", "")).lower()
                if run_type in {"tool", "retriever", "function"}:
                    run_name = run.get("name")
                    if isinstance(run_name, str) and run_name:
                        tools_available.append(run_name)

                if model is None:
                    extra = run.get("extra", {})
                    if isinstance(extra, dict):
                        invocation = extra.get("invocation_params", {})
                        if isinstance(invocation, dict):
                            model = invocation.get("model_name") or invocation.get("model")

                if context_window_tokens is None:
                    for key in ["context_window_tokens", "context_window", "context_limit", "max_context_tokens"]:
                        raw = run.get(key)
                        if isinstance(raw, (int, float, str)):
                            try:
                                context_window_tokens = int(float(raw))
                                break
                            except (TypeError, ValueError):
                                continue

        for key in ["context_window_tokens", "context_window", "context_limit", "max_context_tokens"]:
            raw = data.get(key)
            if isinstance(raw, (int, float, str)):
                try:
                    context_window_tokens = int(float(raw))
                    break
                except (TypeError, ValueError):
                    continue

        return EnvironmentInfo(
            agent_framework="langchain",
            model=model,
            tools_available=sorted(set(tools_available)),
            context_window_tokens=context_window_tokens,
        )

    def _extract_task_context(self, data: dict[str, Any]) -> TaskContext | None:
        """Extract task context."""
        goal = None

        # Check common input locations
        for key in ["input", "inputs", "query", "question", "prompt"]:
            if key in data:
                val = data[key]
                if isinstance(val, str):
                    goal = val
                elif isinstance(val, dict):
                    goal = (
                        val.get("input")
                        or val.get("query")
                        or val.get("question")
                        or val.get("prompt")
                        or val.get("goal")
                    )
                break

        if not goal and "runs" in data and isinstance(data["runs"], list):
            for run in data["runs"]:
                if not isinstance(run, dict):
                    continue
                inputs = run.get("inputs")
                if isinstance(inputs, dict):
                    goal = (
                        inputs.get("input")
                        or inputs.get("query")
                        or inputs.get("question")
                        or inputs.get("prompt")
                    )
                    if goal:
                        break

        if not goal:
            return None

        return TaskContext(
            goal=goal,
            success_criteria=data.get("success_criteria"),
            expected_output_type=data.get("expected_output_type"),
        )

    def _extract_events(self, data: dict[str, Any]) -> list[TraceEvent]:
        """Extract and normalize events."""
        events = []
        event_id = 0

        # Handle flat run structure (single run with child_runs)
        if "child_runs" in data or "run_type" in data:
            self._append_run_tree(
                run=data,
                start_event_id=event_id,
                parent_event_id=None,
                children_by_parent={},
                visited=set(),
                out_events=events,
            )
            return events

        # Handle runs list structure
        if "runs" in data and isinstance(data["runs"], list):
            runs = [run for run in data["runs"] if isinstance(run, dict)]
            by_id: dict[str, dict[str, Any]] = {}
            children_by_parent: dict[str, list[dict[str, Any]]] = {}
            roots: list[dict[str, Any]] = []

            for run in runs:
                rid = self._run_identifier(run)
                if rid:
                    by_id[rid] = run

            for run in runs:
                parent_run_id = run.get("parent_run_id")
                parent_key = str(parent_run_id) if parent_run_id is not None else ""
                if parent_key and parent_key in by_id:
                    children_by_parent.setdefault(parent_key, []).append(run)
                else:
                    roots.append(run)

            visited: set[int] = set()
            for root in roots:
                event_id = self._append_run_tree(
                    run=root,
                    start_event_id=event_id,
                    parent_event_id=None,
                    children_by_parent=children_by_parent,
                    visited=visited,
                    out_events=events,
                )

            # Fallback for orphaned/disconnected runs
            for run in runs:
                if id(run) not in visited:
                    event_id = self._append_run_tree(
                        run=run,
                        start_event_id=event_id,
                        parent_event_id=None,
                        children_by_parent=children_by_parent,
                        visited=visited,
                        out_events=events,
                    )

            return events

        # Handle callback-event format
        if "callbacks" in data and isinstance(data["callbacks"], list):
            return self._extract_callback_events(data["callbacks"])

        # Handle events list directly
        if "events" in data and isinstance(data["events"], list):
            for raw in data["events"]:
                if not isinstance(raw, dict):
                    continue
                parsed = self._parse_event(raw, event_id)
                events.extend(parsed)
                event_id += len(parsed)

        return events

    def _parse_run(
        self, run: dict[str, Any], start_id: int, parent_id: int | None
    ) -> list[TraceEvent]:
        """Parse a LangChain run into TraceEvents."""
        events = []

        run_type = (run.get("run_type") or "").lower()
        serialized = run.get("serialized") or {}
        name = run.get("name") or serialized.get("name")

        # Determine event type from run_type
        if run_type in ["llm", "chat_model"]:
            event_type = EventType.LLM_CALL
        elif run_type in ["tool", "function", "retriever"]:
            event_type = EventType.TOOL_CALL
        elif run_type in ["chain", "agent"]:
            event_type = EventType.DECISION
        else:
            event_type = EventType.MESSAGE

        # Extract error
        error = None
        if run.get("error"):
            error_data = run["error"]
            if isinstance(error_data, str):
                error = EventError(message=error_data)
            elif isinstance(error_data, dict):
                error = EventError(
                    message=error_data.get("message", str(error_data)),
                    stack=error_data.get("traceback"),
                    category=error_data.get("type"),
                )

        # Extract input/output
        inputs = run.get("inputs") or run.get("input")
        outputs = run.get("outputs") or run.get("output")

        # Calculate latency
        latency_ms = None
        start_time = self._parse_timestamp(run.get("start_time"))
        end_time = self._parse_timestamp(run.get("end_time"))
        if start_time and end_time:
            latency_ms = (end_time - start_time).total_seconds() * 1000

        # Extract token counts
        token_count = None
        token_usage = (
            run.get("token_usage")
            or run.get("usage_metadata")
            or (run.get("llm_output") or {}).get("token_usage")
            or (run.get("response_metadata") or {}).get("token_usage")
        )
        if isinstance(token_usage, dict):
            token_count = (
                token_usage.get("total_tokens")
                or token_usage.get("total")
                or token_usage.get("output_tokens")
                or token_usage.get("completion_tokens")
            )
            if token_count is not None:
                try:
                    token_count = int(float(token_count))
                except (TypeError, ValueError):
                    token_count = None

        event = TraceEvent(
            event_id=start_id,
            parent_event_id=parent_id,
            span_id=run.get("id") or run.get("run_id"),
            agent_id=(
                run.get("agent_id")
                or run.get("agent")
                or run.get("name")
            ),
            type=event_type,
            role=EventRole.ASSISTANT if event_type == EventType.LLM_CALL else None,
            name=name,
            input=inputs,
            output=outputs,
            token_count=token_count,
            latency_ms=latency_ms,
            timestamp=start_time or end_time,
            error=error,
            metadata={
                **(run.get("extra", {}) if isinstance(run.get("extra"), dict) else {}),
                "run_type": run_type,
            },
        )
        events.append(event)

        return events

    def _parse_event(self, raw: dict[str, Any], start_id: int) -> list[TraceEvent]:
        """Parse a raw event dict into TraceEvents."""
        event_type = self._determine_event_type(raw)
        role = self._determine_role(raw)

        # Extract error
        error = None
        if "error" in raw:
            error_data = raw["error"]
            if isinstance(error_data, str):
                error = EventError(message=error_data)
            elif isinstance(error_data, dict):
                error = EventError(
                    message=error_data.get("message", str(error_data)),
                    stack=error_data.get("traceback"),
                )

        parent_value = raw.get("parent_id")
        if parent_value is None:
            parent_value = raw.get("parent_run_id")

        event = TraceEvent(
            event_id=start_id,
            parent_event_id=self._safe_parent_event_id(parent_value),
            span_id=raw.get("run_id") or raw.get("id"),
            agent_id=raw.get("agent_id") or raw.get("agent") or raw.get("name"),
            type=event_type,
            role=role,
            name=raw.get("name") or raw.get("tool"),
            input=raw.get("input") or raw.get("inputs"),
            output=raw.get("output") or raw.get("outputs"),
            token_count=raw.get("token_count") or raw.get("tokens"),
            latency_ms=raw.get("latency_ms") or raw.get("duration_ms"),
            timestamp=self._parse_timestamp(raw.get("timestamp") or raw.get("start_time")),
            error=error,
            metadata=raw.get("metadata") or raw.get("extra", {}),
        )
        return [event]

    def _determine_event_type(self, raw: dict[str, Any]) -> EventType:
        """Determine event type from raw data."""
        run_type = str(raw.get("run_type", "") or raw.get("type", "")).lower()

        if run_type in ["llm", "chat_model", "model"]:
            return EventType.LLM_CALL
        if run_type in ["tool", "function", "tool_call", "retriever"]:
            return EventType.TOOL_CALL
        if run_type in ["chain", "agent", "decision"]:
            return EventType.DECISION
        if run_type in ["error", "exception"]:
            return EventType.ERROR

        # Infer from content
        if "error" in raw:
            return EventType.ERROR
        if "tool" in raw or "function" in raw:
            return EventType.TOOL_CALL

        return EventType.MESSAGE

    def _safe_parent_event_id(self, value: Any) -> int | None:
        """Convert parent IDs to int when possible; otherwise drop unresolved IDs."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _run_identifier(self, run: dict[str, Any]) -> str:
        """Get a stable string identifier for a run record."""
        raw = run.get("id") or run.get("run_id")
        return str(raw) if raw is not None else ""

    def _append_run_tree(
        self,
        run: dict[str, Any],
        start_event_id: int,
        parent_event_id: int | None,
        children_by_parent: dict[str, list[dict[str, Any]]],
        visited: set[int],
        out_events: list[TraceEvent],
    ) -> int:
        """Append a run and all known descendants to out_events, returning next event ID."""
        if id(run) in visited:
            return start_event_id

        visited.add(id(run))
        parsed = self._parse_run(run, start_event_id, parent_event_id)
        out_events.extend(parsed)
        current_event_id = start_event_id
        next_event_id = start_event_id + len(parsed)

        run_id = self._run_identifier(run)
        child_runs: list[dict[str, Any]] = []
        if run_id:
            child_runs.extend(children_by_parent.get(run_id, []))
        child_runs.extend(
            child
            for child in run.get("child_runs", [])
            if isinstance(child, dict)
        )

        for child in child_runs:
            next_event_id = self._append_run_tree(
                run=child,
                start_event_id=next_event_id,
                parent_event_id=current_event_id,
                children_by_parent=children_by_parent,
                visited=visited,
                out_events=out_events,
            )
        return next_event_id

    def _extract_callback_events(self, callbacks: list[Any]) -> list[TraceEvent]:
        """Parse callback-style events into normalized trace events."""
        events: list[TraceEvent] = []
        event_id = 0
        run_id_to_event_id: dict[str, int] = {}

        for callback in callbacks:
            if not isinstance(callback, dict):
                continue

            run_id = callback.get("run_id") or callback.get("id")
            parent_run_id = callback.get("parent_run_id")

            raw = self._callback_to_raw_event(callback)
            if raw is None:
                continue

            if parent_run_id is not None:
                mapped_parent = run_id_to_event_id.get(str(parent_run_id))
                if mapped_parent is not None:
                    raw["parent_id"] = mapped_parent

            parsed = self._parse_event(raw, event_id)
            events.extend(parsed)
            if run_id is not None and parsed:
                run_id_to_event_id[str(run_id)] = parsed[0].event_id
            event_id += len(parsed)

        return events

    def _callback_to_raw_event(self, callback: dict[str, Any]) -> dict[str, Any] | None:
        """Convert callback payloads into a generic event-shaped dictionary."""
        event_name = str(callback.get("event") or callback.get("type") or "").lower()
        if not event_name:
            return None
        if event_name.startswith("on_"):
            event_name = event_name[3:]
        event_name = event_name.replace("_start", "").replace("_end", "")

        event_type = "message"
        if any(k in event_name for k in ["llm", "chat", "model"]):
            event_type = "llm"
        elif any(k in event_name for k in ["tool", "retriever", "function"]):
            event_type = "tool"
        elif any(k in event_name for k in ["chain", "agent"]):
            event_type = "chain"
        elif "error" in event_name:
            event_type = "error"

        token_count = callback.get("token_count") or callback.get("tokens")
        if token_count is None and isinstance(callback.get("usage"), dict):
            usage = callback["usage"]
            token_count = (
                usage.get("total_tokens")
                or usage.get("total")
                or usage.get("completion_tokens")
                or usage.get("output_tokens")
            )

        return {
            "type": event_type,
            "run_id": callback.get("run_id") or callback.get("id"),
            "name": callback.get("name") or callback.get("tool_name") or event_name,
            "input": callback.get("input") or callback.get("inputs") or callback.get("prompt"),
            "output": callback.get("output") or callback.get("outputs") or callback.get("result"),
            "token_count": token_count,
            "latency_ms": callback.get("latency_ms") or callback.get("duration_ms"),
            "timestamp": callback.get("timestamp") or callback.get("time"),
            "error": callback.get("error"),
            "metadata": callback.get("metadata") or callback.get("extra", {}),
        }

    def _determine_role(self, raw: dict[str, Any]) -> EventRole | None:
        """Determine role from raw data."""
        role = str(raw.get("role", "")).lower()

        role_mapping = {
            "system": EventRole.SYSTEM,
            "user": EventRole.USER,
            "human": EventRole.USER,
            "assistant": EventRole.ASSISTANT,
            "ai": EventRole.ASSISTANT,
            "tool": EventRole.TOOL,
            "function": EventRole.TOOL,
        }
        return role_mapping.get(role)

    def _extract_final_output(self, data: dict[str, Any]) -> str | dict | None:
        """Extract final output."""
        for key in ["output", "outputs", "result", "response", "answer"]:
            if key in data:
                return data[key]

        # Try from last run
        if "runs" in data and data["runs"]:
            last_run = data["runs"][-1]
            if isinstance(last_run, dict):
                return last_run.get("outputs") or last_run.get("output")

        return None

    def _extract_error_summary(
        self, data: dict[str, Any], events: list[TraceEvent]
    ) -> str | None:
        """Extract error summary."""
        if "error" in data:
            error = data["error"]
            if isinstance(error, str):
                return error
            if isinstance(error, dict):
                return error.get("message", str(error))

        # Collect from events
        errors = [e.error.message for e in events if e.error]
        if errors:
            return "; ".join(errors[:3])

        return None
