"""
TraceSchemaV2 - Pydantic models for the normalized trace schema.

This schema supports:
- Multiple event types (llm_call, tool_call, decision, error, message)
- Nested events via parent_event_id
- Span tracking for OpenTelemetry compatibility
- Task context for drift analysis
- Comprehensive statistics
"""

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TraceStatus(str, Enum):
    """Status of the trace execution."""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    LOOP_DETECTED = "loop_detected"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"  # missing, in-progress or unrecognised: not a verified completion


class EventType(str, Enum):
    """Type of event in the trace."""
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    ERROR = "error"
    MESSAGE = "message"


class EventRole(str, Enum):
    """Role of the event participant."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class EventError(BaseModel):
    """Error information for an event."""
    message: str
    stack: str | None = None
    category: str | None = None


def _convert_latency_to_int(v: Any) -> int | None:
    """Convert float/str latency values to int (rounding); None on garbage."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        return int(round(float(v)))
    except (ValueError, TypeError, OverflowError):
        return None


class TraceEvent(BaseModel):
    """
    A single event in the trace.

    Events can be nested via parent_event_id and tracked with span_id
    for OpenTelemetry compatibility.
    """
    event_id: int
    parent_event_id: int | None = None
    span_id: str | None = None
    agent_id: str | None = None

    type: EventType
    role: EventRole | None = None

    name: str | None = None  # tool name / node name / model name
    input: str | dict[str, Any] | list[Any] | None = None
    output: str | dict[str, Any] | list[Any] | None = None

    token_count: int | None = None
    latency_ms: int | None = None
    timestamp: datetime | None = None

    error: EventError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latency_ms", mode="before")
    @classmethod
    def convert_latency_to_int(cls, v: Any) -> int | None:
        """Convert float latency values to int (rounding)."""
        return _convert_latency_to_int(v)

    def is_error(self) -> bool:
        """Check if this event is an error or contains an error."""
        return self.type == EventType.ERROR or self.error is not None

    def get_tool_signature(self) -> str | None:
        """Get a signature for tool calls (name + input digest) for loop detection."""
        if self.type != EventType.TOOL_CALL or not self.name:
            return None
        try:
            payload = json.dumps(self.input, sort_keys=True, default=str)
        except (TypeError, ValueError):
            payload = str(self.input)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"{self.name}:{digest}"


class TaskContext(BaseModel):
    """
    Task context for drift analysis.

    Contains the goal, success criteria, and expected output type
    to help identify when an agent has drifted from its objective.
    """
    goal: str | None = None
    success_criteria: list[str] | None = None
    expected_output_type: str | None = None  # json, markdown, tool_result, free_text


class EnvironmentInfo(BaseModel):
    """
    Environment information for the trace.

    Contains the agent framework, model, and available tools
    for hallucinated tool detection.
    """
    agent_framework: str  # langgraph, langchain, autogen, crewai, other
    model: str | None = None
    tools_available: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = None


class TraceStats(BaseModel):
    """Aggregate statistics for the trace."""
    total_tokens: int | None = None
    total_latency_ms: int | None = None
    num_llm_calls: int = 0
    num_tool_calls: int = 0
    num_errors: int = 0

    @field_validator("total_latency_ms", mode="before")
    @classmethod
    def convert_latency_to_int(cls, v: Any) -> int | None:
        """Convert float latency values to int (rounding)."""
        return _convert_latency_to_int(v)


class Trace(BaseModel):
    """
    The complete normalized trace.

    This is the main schema used throughout the Agent Autopsy system.
    All trace formats (LangGraph, LangChain, etc.) are normalized to this schema.
    """
    run_id: str
    trace_id: str | None = None  # for OTEL compatibility

    timestamp_start: datetime
    timestamp_end: datetime | None = None

    status: TraceStatus

    task: TaskContext | None = None
    env: EnvironmentInfo

    events: list[TraceEvent] = Field(default_factory=list)

    final_output: str | dict[str, Any] | None = None
    error_summary: str | None = None

    stats: TraceStats = Field(default_factory=TraceStats)

    def get_event(self, event_id: int) -> TraceEvent | None:
        """Get an event by ID."""
        for event in self.events:
            if event.event_id == event_id:
                return event
        return None

    def get_events_by_type(self, event_type: EventType) -> list[TraceEvent]:
        """Get all events of a specific type."""
        return [e for e in self.events if e.type == event_type]

    def get_agent_ids(self) -> list[str]:
        """Get all unique agent IDs present in the trace."""
        return sorted({e.agent_id for e in self.events if e.agent_id})

    def get_events_by_agent(self, agent_id: str) -> list[TraceEvent]:
        """Get all events for a given agent ID."""
        return [e for e in self.events if e.agent_id == agent_id]

    def get_agent_handoffs(self) -> list[tuple[int, str, str]]:
        """
        Detect agent handoff boundaries.

        Returns:
            List of tuples: (event_id, from_agent, to_agent)
        """
        handoffs: list[tuple[int, str, str]] = []
        last_agent: str | None = None
        for event in self.events:
            current = event.agent_id
            if current and last_agent and current != last_agent:
                handoffs.append((event.event_id, last_agent, current))
            if current:
                last_agent = current
        return handoffs

    def get_error_events(self) -> list[TraceEvent]:
        """Get all events that are errors or contain errors."""
        return [e for e in self.events if e.is_error()]

    def get_tool_calls(self) -> list[TraceEvent]:
        """Get all tool call events."""
        return self.get_events_by_type(EventType.TOOL_CALL)

    def get_llm_calls(self) -> list[TraceEvent]:
        """Get all LLM call events."""
        return self.get_events_by_type(EventType.LLM_CALL)

    def get_events_in_range(self, start_id: int, end_id: int) -> list[TraceEvent]:
        """Get events within an ID range (inclusive)."""
        return [e for e in self.events if start_id <= e.event_id <= end_id]

    def calculate_stats(self) -> TraceStats:
        """Recalculate statistics from events."""
        stats = TraceStats()

        for event in self.events:
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
