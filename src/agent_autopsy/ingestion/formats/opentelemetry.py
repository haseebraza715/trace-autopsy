"""
OpenTelemetry trace parser.

Parses OpenTelemetry (OTEL) span data into the normalized TraceSchemaV2 format.
Handles OTEL JSON export format with resourceSpans, scopeSpans, and spans.
"""

import hashlib
from datetime import datetime
from typing import Any

from agent_autopsy.ingestion.parser import TraceParser
from agent_autopsy.schema import (
    EnvironmentInfo,
    EventError,
    EventType,
    TaskContext,
    Trace,
    TraceEvent,
    TraceStatus,
)


class OpenTelemetryParser(TraceParser):
    """Parser for OpenTelemetry trace format."""

    def can_parse(self, data: dict[str, Any]) -> bool:
        """Check if this is an OpenTelemetry trace."""
        # Standard OTEL export format
        if "resourceSpans" in data:
            return True
        # Simplified span format
        if "traceId" in data and "spans" in data:
            return True
        # Single span with OTEL fields
        if "spanId" in data and "traceId" in data:
            return True
        # Check for OTEL-style attributes
        if "attributes" in data and isinstance(data["attributes"], list):
            for attr in data["attributes"]:
                if isinstance(attr, dict) and "key" in attr:
                    return True
        return False

    def parse(self, data: dict[str, Any]) -> Trace:
        """Parse OpenTelemetry trace into normalized format."""
        # Extract all spans from various OTEL structures
        spans = self._extract_all_spans(data)

        # Extract trace ID
        trace_id = self._extract_trace_id(data, spans)

        # Extract timestamps from spans
        timestamp_start, timestamp_end = self._extract_timestamps(spans)

        # Extract status from spans
        status = self._extract_status(spans)

        # Extract environment info
        env = self._extract_environment(data, spans)

        # Extract task context
        task = self._extract_task_context(data, spans)

        # Convert spans to events
        events = self._spans_to_events(spans)

        # Extract final output and error summary
        final_output = self._extract_final_output(spans)
        error_summary = self._extract_error_summary(spans, events)

        # Build trace
        trace = Trace(
            run_id=trace_id,
            trace_id=trace_id,
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

    def _extract_all_spans(self, data: dict[str, Any]) -> list[dict]:
        """Extract all spans from various OTEL structures."""
        spans = []

        # Standard OTEL export format: resourceSpans -> scopeSpans -> spans
        if "resourceSpans" in data:
            for resource_span in data["resourceSpans"]:
                if not isinstance(resource_span, dict):
                    continue
                scope_spans = resource_span.get("scopeSpans") or resource_span.get("instrumentationLibrarySpans") or []
                for scope_span in scope_spans:
                    if not isinstance(scope_span, dict):
                        continue
                    for span in scope_span.get("spans", []):
                        if isinstance(span, dict):
                            # Add resource attributes to span
                            resource = resource_span.get("resource", {})
                            span["_resource"] = resource
                            span["_scope"] = scope_span.get("scope") or scope_span.get("instrumentationLibrary")
                            spans.append(span)

        # Simplified format with spans array
        elif "spans" in data and isinstance(data["spans"], list):
            spans = [s for s in data["spans"] if isinstance(s, dict)]

        # Single span
        elif "spanId" in data:
            spans = [data]

        return spans

    def _extract_trace_id(self, data: dict[str, Any], spans: list[dict]) -> str:
        """Extract trace ID."""
        # Try from root data
        if "traceId" in data:
            return str(data["traceId"])

        # Try from first span
        if spans and "traceId" in spans[0]:
            return str(spans[0]["traceId"])

        # Generate from content
        return hashlib.md5(str(data).encode()).hexdigest()[:12]

    def _extract_timestamps(
        self, spans: list[dict]
    ) -> tuple[datetime, datetime | None]:
        """Extract start and end timestamps from spans."""
        start = None
        end = None

        for span in spans:
            span_start = self._parse_otel_timestamp(span.get("startTimeUnixNano"))
            span_end = self._parse_otel_timestamp(span.get("endTimeUnixNano"))

            if span_start:
                if start is None or span_start < start:
                    start = span_start
            if span_end:
                if end is None or span_end > end:
                    end = span_end

        if start is None:
            start = datetime.now()

        return start, end

    def _parse_otel_timestamp(self, value: Any) -> datetime | None:
        """Parse OTEL nanosecond timestamp; never raises on bad values."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            try:
                # OTEL uses nanoseconds
                if value > 1e18:  # Nanoseconds
                    return datetime.fromtimestamp(value / 1e9)
                elif value > 1e15:  # Microseconds
                    return datetime.fromtimestamp(value / 1e6)
                elif value > 1e12:  # Milliseconds
                    return datetime.fromtimestamp(value / 1e3)
                else:  # Seconds
                    return datetime.fromtimestamp(value)
            except (OSError, ValueError, OverflowError):
                return None

        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    return datetime.fromtimestamp(int(value) / 1e9)
                except (ValueError, TypeError, OSError, OverflowError):
                    pass

        return None

    def _extract_status(self, spans: list[dict]) -> TraceStatus:
        """Extract trace status from spans."""
        for span in spans:
            status = span.get("status", {})
            if isinstance(status, dict):
                code = status.get("code") or status.get("statusCode")
                if code == 2 or code == "STATUS_CODE_ERROR":  # OTEL error status
                    return TraceStatus.FAILED
                if status.get("message"):
                    return TraceStatus.FAILED

            # Check for error events
            events = span.get("events", [])
            for event in events:
                if isinstance(event, dict):
                    name = event.get("name", "").lower()
                    if "error" in name or "exception" in name:
                        return TraceStatus.FAILED

        return TraceStatus.SUCCESS

    def _extract_environment(
        self, data: dict[str, Any], spans: list[dict]
    ) -> EnvironmentInfo:
        """Extract environment info from OTEL data."""
        tools = []
        model = None
        framework = "opentelemetry"
        context_window_tokens = None

        # Extract from resource attributes
        if "resourceSpans" in data:
            for rs in data["resourceSpans"]:
                resource = rs.get("resource", {})
                for attr in resource.get("attributes", []):
                    if not isinstance(attr, dict):
                        continue
                    key = attr.get("key", "")
                    value = self._get_attr_value(attr)
                    key_lower = key.lower()

                    # service.name is the application name, not a framework;
                    # only a real semantic-convention framework marker (e.g.
                    # gen_ai.system, telemetry.sdk.name) may replace the default.
                    if "gen_ai.system" in key_lower:
                        framework = str(value) or framework
                    if "model" in key_lower:
                        model = value
                    if context_window_tokens is None and any(
                        t in key_lower for t in ["context_window", "context_limit", "max_context", "max_input_tokens"]
                    ):
                        if isinstance(value, (int, float, str)):
                            try:
                                context_window_tokens = int(float(value))
                            except (TypeError, ValueError):
                                pass

        # Extract from span attributes
        for span in spans:
            attrs = self._flatten_attributes(span.get("attributes", []))
            for key, value in attrs.items():
                key_lower = key.lower()
                if any(k in key_lower for k in ["tool.name", "tool", "function.name"]) and value:
                    if isinstance(value, list):
                        tools.extend(str(v) for v in value if v is not None)
                    else:
                        tools.append(str(value))
                if "gen_ai.system" in key_lower:
                    framework = str(value)
                if "model" in key_lower and not model and value:
                    model = str(value)
                if context_window_tokens is None and any(
                    t in key_lower for t in ["context_window", "context_limit", "max_context", "max_input_tokens"]
                ):
                    if isinstance(value, (int, float, str)):
                        try:
                            context_window_tokens = int(float(value))
                        except (TypeError, ValueError):
                            pass

        return EnvironmentInfo(
            agent_framework=framework,
            model=model,
            tools_available=sorted(set(tools)),
            context_window_tokens=context_window_tokens,
        )

    def _get_attr_value(self, attr: dict) -> Any:
        """Extract value from OTEL attribute."""
        if "value" in attr:
            value = attr["value"]
            if isinstance(value, dict):
                # OTEL uses typed values like stringValue, intValue, etc.
                for vtype in ["stringValue", "intValue", "boolValue", "doubleValue"]:
                    if vtype in value:
                        return value[vtype]
                if "arrayValue" in value:
                    arr = value["arrayValue"]
                    if isinstance(arr, dict) and "values" in arr:
                        return [self._get_attr_value({"value": v}) for v in arr["values"]]
            return value
        return None

    def _extract_task_context(
        self, data: dict[str, Any], spans: list[dict]
    ) -> TaskContext | None:
        """Extract task context from spans."""
        goal = None

        # Look for input/query in span attributes
        for span in spans:
            attrs = self._flatten_attributes(span.get("attributes", []))
            for key, value in attrs.items():
                key_lower = key.lower()
                if any(k in key_lower for k in ["input", "query", "prompt", "question"]):
                    if isinstance(value, str) and len(value) > 5:
                        goal = value
                        break
            if goal:
                break

        if not goal:
            return None

        return TaskContext(goal=goal)

    def _spans_to_events(self, spans: list[dict]) -> list[TraceEvent]:
        """Convert OTEL spans to TraceEvents."""
        events = []
        span_id_map: dict[str, int] = {}  # Map spanId -> event_id for parent linking

        # First pass: create events and build ID map
        for i, span in enumerate(spans):
            span_id = span.get("spanId")
            if span_id:
                span_id_map[str(span_id)] = i

        # Second pass: create TraceEvents with correct parent_event_id
        for i, span in enumerate(spans):
            attrs = self._flatten_attributes(span.get("attributes", []))
            event_type = self._determine_span_type(span, attrs)

            # Map parent span to parent event
            parent_span_id = span.get("parentSpanId")
            parent_event_id = span_id_map.get(str(parent_span_id)) if parent_span_id else None

            # Extract timing
            start_time = self._parse_otel_timestamp(span.get("startTimeUnixNano"))
            end_time = self._parse_otel_timestamp(span.get("endTimeUnixNano"))
            latency_ms = None
            if start_time and end_time:
                latency_ms = (end_time - start_time).total_seconds() * 1000

            # Extract input/output from attributes
            input_data = None
            output_data = None
            token_count = None
            name = span.get("name")

            for key, value in attrs.items():
                key_lower = key.lower()
                # Numeric telemetry attrs (gen_ai.request.max_tokens etc.)
                # share these keywords and must never become payloads.
                if (
                    input_data is None
                    and isinstance(value, (str, dict, list))
                    and any(k in key_lower for k in ["input", "prompt", "query", "request"])
                ):
                    input_data = value
                elif (
                    output_data is None
                    and isinstance(value, (str, dict, list))
                    and any(k in key_lower for k in ["output", "response", "result", "completion"])
                ):
                    output_data = value
                if token_count is None and "token" in key_lower:
                    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                        try:
                            token_count = int(float(value))
                        except (TypeError, ValueError):
                            token_count = None

            # Extract error from status
            error = None
            status = span.get("status", {})
            if isinstance(status, dict) and status.get("message"):
                error = EventError(
                    message=status["message"],
                    category=status.get("code"),
                )
            elif isinstance(status, dict):
                code = status.get("code") or status.get("statusCode")
                if code in (2, "2", "STATUS_CODE_ERROR"):
                    error = EventError(message="Span marked as error", category=str(code))

            # Check for error events
            for span_event in span.get("events", []):
                if isinstance(span_event, dict):
                    event_name = span_event.get("name", "").lower()
                    if "error" in event_name or "exception" in event_name:
                        # Extract error details from event attributes
                        for attr in span_event.get("attributes", []):
                            if not isinstance(attr, dict):
                                continue
                            key = attr.get("key", "").lower()
                            value = self._get_attr_value(attr)
                            if "message" in key:
                                error = EventError(message=str(value))
                            elif "stacktrace" in key or "stack" in key:
                                if error:
                                    error.stack = str(value)

            # Build metadata from attributes and span identifiers
            metadata: dict[str, Any] = {
                **attrs,
                "trace_id": span.get("traceId"),
                "span_id": span.get("spanId"),
                "parent_span_id": span.get("parentSpanId"),
            }

            event = TraceEvent(
                event_id=i,
                parent_event_id=parent_event_id,
                span_id=span.get("spanId"),
                agent_id=(
                    str(attrs.get("agent.id"))
                    if attrs.get("agent.id") is not None
                    else (
                        str(attrs.get("gen_ai.agent.name"))
                        if attrs.get("gen_ai.agent.name") is not None
                        else None
                    )
                ),
                type=event_type,
                name=name,
                input=input_data,
                output=output_data,
                token_count=token_count,
                latency_ms=latency_ms,
                timestamp=start_time,
                error=error,
                metadata=metadata,
            )
            events.append(event)

        return events

    def _determine_span_type(self, span: dict, attrs: dict[str, Any] | None = None) -> EventType:
        """Determine event type from span."""
        name = str(span.get("name", "")).lower()
        attrs = attrs or self._flatten_attributes(span.get("attributes", []))

        # Check span kind
        kind = span.get("kind")
        if kind == 3:  # CLIENT - often LLM calls
            if any(k in name for k in ["llm", "model", "chat", "completion"]):
                return EventType.LLM_CALL

        # Infer from name
        if any(k in name for k in ["llm", "model", "chat", "completion", "openai", "anthropic"]):
            return EventType.LLM_CALL
        if any(k in name for k in ["tool", "function", "action", "retriever"]):
            return EventType.TOOL_CALL
        if any(k in name for k in ["decision", "router", "branch"]):
            return EventType.DECISION
        if any(k in name for k in ["error", "exception"]):
            return EventType.ERROR

        # Check attributes
        for key in attrs.keys():
            key_lower = key.lower()
            if "gen_ai." in key_lower or "llm" in key_lower or "model" in key_lower:
                return EventType.LLM_CALL
            if "tool" in key_lower or "function" in key_lower or "retriever" in key_lower:
                return EventType.TOOL_CALL

        return EventType.MESSAGE

    def _extract_final_output(self, spans: list[dict]) -> str | dict | None:
        """Extract final output from spans."""
        # Find the root span (no parent) with output
        for span in spans:
            if not span.get("parentSpanId"):
                attrs = self._flatten_attributes(span.get("attributes", []))
                for key, value in attrs.items():
                    if isinstance(value, (str, dict)) and any(
                        k in key.lower() for k in ["output", "response", "result", "completion"]
                    ):
                        return value

        # Try last span
        if spans:
            attrs = self._flatten_attributes(spans[-1].get("attributes", []))
            for key, value in attrs.items():
                if isinstance(value, (str, dict)) and any(
                    k in key.lower() for k in ["output", "response", "completion"]
                ):
                    return value

        return None

    def _extract_error_summary(
        self, spans: list[dict], events: list[TraceEvent]
    ) -> str | None:
        """Extract error summary."""
        errors = []

        for span in spans:
            status = span.get("status", {})
            if isinstance(status, dict) and status.get("message"):
                errors.append(status["message"])

        # Also collect from events
        errors.extend([e.error.message for e in events if e.error])

        if errors:
            return "; ".join(errors[:3])

        return None

    def _flatten_attributes(self, attributes: list[Any]) -> dict[str, Any]:
        """Convert OTEL attribute list into a flat key/value dict."""
        flat: dict[str, Any] = {}
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            key = attr.get("key")
            if key is None:
                continue
            flat[str(key)] = self._get_attr_value(attr)
        return flat
