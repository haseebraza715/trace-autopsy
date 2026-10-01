"""
Trace capture callback handler for LangChain/LangGraph.

Captures agent execution events and saves them as machine-readable JSON traces.
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from agent_autopsy.utils.atomic import atomic_write_json

# Secret keys pattern for redaction
SECRET_KEYS_PATTERN = re.compile(
    r"(api_key|authorization|token|secret|password|credential|openrouter_api_key)",
    re.IGNORECASE
)
# Long whitespace-free tokens: JWTs, sk-/pk-/ghp_ keys, hashes, etc.
_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_\-\.]{20,}$")

logger = logging.getLogger(__name__)


@dataclass
class TraceConfig:
    """Configuration for trace capture."""
    enabled: bool = True
    trace_dir: Path = field(default_factory=lambda: Path("./traces"))
    max_chars: int = 5000

    @classmethod
    def from_env(cls) -> "TraceConfig":
        """Load config from environment variables."""
        return cls(
            enabled=os.getenv("TRACE_ENABLED", "1").lower() in ("1", "true", "yes"),
            trace_dir=Path(os.getenv("TRACE_DIR", "./traces")),
            max_chars=int(os.getenv("TRACE_MAX_CHARS", "5000")),
        )


def get_trace_config() -> TraceConfig:
    """Get trace configuration from environment."""
    return TraceConfig.from_env()


def _redact_secrets(data: Any, visited: set | None = None) -> Any:
    """
    Redact sensitive values from data.

    Replaces values for keys matching secret patterns with '***'.
    """
    if visited is None:
        visited = set()

    # Prevent infinite recursion
    obj_id = id(data)
    if obj_id in visited:
        return data
    visited.add(obj_id)

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and SECRET_KEYS_PATTERN.search(key):
                result[key] = "***"
            else:
                result[key] = _redact_secrets(value, visited)
        return result
    elif isinstance(data, list):
        return [_redact_secrets(item, visited) for item in data]
    elif isinstance(data, str):
        # Redact inline values that are shaped like secrets (long
        # whitespace-free tokens or key-like strings), NOT prose that merely
        # mentions a secret word — e.g. an error like "token budget exceeded"
        # or an LLM output about "authorization" must survive intact.
        if len(data) >= 20 and _TOKEN_VALUE_PATTERN.match(data):
            return "***"
        return data
    else:
        return data


def _safe_serialize(obj: Any, max_chars: int = 5000) -> Any:
    """
    Safely serialize an object to JSON-compatible format.

    - Converts non-serializable objects to repr()
    - Truncates long strings
    - Redacts secrets
    """
    try:
        if obj is None:
            return None
        elif isinstance(obj, (str, int, float, bool)):
            if isinstance(obj, str) and len(obj) > max_chars:
                return obj[:max_chars] + f"... [truncated, {len(obj)} chars total]"
            return obj
        elif isinstance(obj, dict):
            return {
                str(k): _safe_serialize(v, max_chars)
                for k, v in obj.items()
            }
        elif isinstance(obj, (list, tuple)):
            return [_safe_serialize(item, max_chars) for item in obj]
        elif hasattr(obj, "dict"):
            # Pydantic models
            return _safe_serialize(obj.dict(), max_chars)
        elif hasattr(obj, "__dict__"):
            obj_dict = obj.__dict__
            if obj_dict:
                return _safe_serialize(obj_dict, max_chars)
            return repr(obj)
        else:
            # Fallback to repr for unknown types
            repr_str = repr(obj)
            if len(repr_str) > max_chars:
                return repr_str[:max_chars] + "... [truncated]"
            return repr_str
    except (TypeError, ValueError, RecursionError, AttributeError, KeyError) as e:
        logger.warning("Trace serialization issue: %s", e, exc_info=True)
        return f"<serialization error: {e}>"
    except Exception as e:
        logger.exception("Unexpected trace serialization failure")
        return f"<serialization error: {e}>"


class TraceSaver(BaseCallbackHandler):
    """
    LangChain callback handler that captures execution events.

    Collects LLM calls, tool calls, chain/node events, and errors
    into a structured trace format suitable for autopsy analysis.
    """

    _event_listeners: list = []

    def __init__(
        self,
        run_id: str | None = None,
        config: TraceConfig | None = None,
    ):
        super().__init__()
        self.run_id = run_id or str(uuid4())
        self.config = config or get_trace_config()
        self.events: list[dict] = []
        self.start_time = time.time()
        self._event_counter = 0
        self._pending_starts: dict[str, dict] = {}  # Track start events for latency

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _add_event(
        self,
        event_type: str,
        name: str,
        input_data: Any = None,
        output_data: Any = None,
        metadata: dict | None = None,
        latency_ms: float | None = None,
        error: str | None = None,
    ) -> dict:
        """Add an event to the trace."""
        event = {
            "event_id": self._event_counter,
            "ts": self._get_timestamp(),
            "type": event_type,
            "name": name,
        }

        if input_data is not None:
            serialized = _safe_serialize(input_data, self.config.max_chars)
            event["input"] = _redact_secrets(serialized)

        if output_data is not None:
            serialized = _safe_serialize(output_data, self.config.max_chars)
            event["output"] = _redact_secrets(serialized)

        if metadata:
            event["metadata"] = _redact_secrets(metadata)

        if latency_ms is not None:
            event["latency_ms"] = round(latency_ms, 2)

        if error:
            event["error"] = error

        self.events.append(event)
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("TraceSaver event listener failed")
        self._event_counter += 1
        return event

    @classmethod
    def register_event_listener(cls, listener) -> None:
        """Register an in-process listener callback(event_dict)."""
        cls._event_listeners.append(listener)

    @classmethod
    def clear_event_listeners(cls) -> None:
        """Clear registered in-process listeners."""
        cls._event_listeners.clear()

    # -------------------------------------------------------------------------
    # LLM Callbacks
    # -------------------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ) -> None:
        """Called when LLM starts processing."""
        if not self.config.enabled:
            return

        # Handle None serialized
        if serialized is None:
            serialized = {}
        
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1] if isinstance(serialized.get("id"), list) else "unknown")

        self._pending_starts[str(run_id)] = {
            "start_time": time.time(),
            "name": model_name,
        }

        self._add_event(
            event_type="llm_start",
            name=model_name,
            input_data=prompts,
            metadata={
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "tags": tags,
                **(metadata or {}),
            },
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ) -> None:
        """Called when chat model starts processing."""
        if not self.config.enabled:
            return

        # Handle None serialized
        if serialized is None:
            serialized = {}
        
        model_name = serialized.get("name", serialized.get("id", ["unknown"])[-1] if isinstance(serialized.get("id"), list) else "unknown")

        self._pending_starts[str(run_id)] = {
            "start_time": time.time(),
            "name": model_name,
        }

        # Convert messages to serializable format
        msg_data = []
        for msg_list in messages:
            if isinstance(msg_list, list):
                msg_data.append([
                    {"type": type(m).__name__, "content": getattr(m, "content", str(m))}
                    for m in msg_list
                ])
            else:
                msg_data.append({"type": type(msg_list).__name__, "content": getattr(msg_list, "content", str(msg_list))})

        self._add_event(
            event_type="llm_start",
            name=model_name,
            input_data=msg_data,
            metadata={
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "tags": tags,
                "is_chat_model": True,
                **(metadata or {}),
            },
        )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when LLM finishes processing."""
        if not self.config.enabled:
            return

        # Calculate latency
        latency_ms = None
        start_info = self._pending_starts.pop(str(run_id), None)
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            model_name = start_info["name"]
        else:
            model_name = "unknown"

        # Extract token usage
        token_usage = {}
        if response.llm_output:
            if "token_usage" in response.llm_output:
                token_usage = response.llm_output["token_usage"]
            elif "usage" in response.llm_output:
                token_usage = response.llm_output["usage"]

        # Get output content
        output_content = []
        for generations in response.generations:
            for gen in generations:
                if hasattr(gen, "message"):
                    msg = gen.message
                    content = {
                        "type": type(msg).__name__,
                        "content": getattr(msg, "content", ""),
                    }
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        content["tool_calls"] = [
                            {"name": tc.get("name", tc.get("function", {}).get("name", "unknown")),
                             "args": tc.get("args", tc.get("function", {}).get("arguments", {}))}
                            for tc in msg.tool_calls
                        ]
                    output_content.append(content)
                else:
                    output_content.append({"text": gen.text})

        self._add_event(
            event_type="llm_end",
            name=model_name,
            output_data=output_content,
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
                "tokens": token_usage if token_usage else None,
            },
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when LLM encounters an error."""
        if not self.config.enabled:
            return

        start_info = self._pending_starts.pop(str(run_id), None)
        latency_ms = None
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            model_name = start_info["name"]
        else:
            model_name = "unknown"

        self._add_event(
            event_type="error",
            name=model_name,
            error=str(error),
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
                "error_type": type(error).__name__,
            },
        )

    # -------------------------------------------------------------------------
    # Tool Callbacks
    # -------------------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        inputs=None,
        **kwargs,
    ) -> None:
        """Called when a tool starts execution."""
        if not self.config.enabled:
            return

        # Handle None serialized
        if serialized is None:
            serialized = {}
        
        tool_name = serialized.get("name", "unknown_tool")

        self._pending_starts[str(run_id)] = {
            "start_time": time.time(),
            "name": tool_name,
        }

        # Prefer structured inputs over string
        tool_input = inputs if inputs else input_str

        self._add_event(
            event_type="tool_start",
            name=tool_name,
            input_data=tool_input,
            metadata={
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "tags": tags,
                **(metadata or {}),
            },
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when a tool finishes execution."""
        if not self.config.enabled:
            return

        # Calculate latency
        latency_ms = None
        start_info = self._pending_starts.pop(str(run_id), None)
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            tool_name = start_info["name"]
        else:
            tool_name = "unknown_tool"

        self._add_event(
            event_type="tool_end",
            name=tool_name,
            output_data=output,
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
            },
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when a tool encounters an error."""
        if not self.config.enabled:
            return

        start_info = self._pending_starts.pop(str(run_id), None)
        latency_ms = None
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            tool_name = start_info["name"]
        else:
            tool_name = "unknown_tool"

        self._add_event(
            event_type="error",
            name=tool_name,
            error=str(error),
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
                "error_type": type(error).__name__,
            },
        )

    # -------------------------------------------------------------------------
    # Chain Callbacks
    # -------------------------------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ) -> None:
        """Called when a chain starts."""
        if not self.config.enabled:
            return

        # Handle None serialized
        if serialized is None:
            serialized = {}
        
        chain_name = serialized.get("name", serialized.get("id", ["unknown"])[-1] if isinstance(serialized.get("id"), list) else "unknown")

        self._pending_starts[str(run_id)] = {
            "start_time": time.time(),
            "name": chain_name,
        }

        self._add_event(
            event_type="chain_start",
            name=chain_name,
            input_data=inputs,
            metadata={
                "run_id": str(run_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "tags": tags,
                **(metadata or {}),
            },
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when a chain ends."""
        if not self.config.enabled:
            return

        # Calculate latency
        latency_ms = None
        start_info = self._pending_starts.pop(str(run_id), None)
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            chain_name = start_info["name"]
        else:
            chain_name = "unknown"

        self._add_event(
            event_type="chain_end",
            name=chain_name,
            output_data=outputs,
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
            },
        )

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when a chain encounters an error."""
        if not self.config.enabled:
            return

        start_info = self._pending_starts.pop(str(run_id), None)
        latency_ms = None
        if start_info:
            latency_ms = (time.time() - start_info["start_time"]) * 1000
            chain_name = start_info["name"]
        else:
            chain_name = "unknown"

        self._add_event(
            event_type="error",
            name=chain_name,
            error=str(error),
            latency_ms=latency_ms,
            metadata={
                "run_id": str(run_id),
                "error_type": type(error).__name__,
            },
        )

    # -------------------------------------------------------------------------
    # Agent Callbacks
    # -------------------------------------------------------------------------

    def on_agent_action(
        self,
        action: AgentAction,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when an agent takes an action."""
        if not self.config.enabled:
            return

        self._add_event(
            event_type="decision",
            name="agent_action",
            input_data={
                "tool": action.tool,
                "tool_input": action.tool_input,
            },
            metadata={
                "run_id": str(run_id),
                "log": action.log if hasattr(action, "log") else None,
            },
        )

    def on_agent_finish(
        self,
        finish: AgentFinish,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """Called when an agent finishes."""
        if not self.config.enabled:
            return

        self._add_event(
            event_type="message",
            name="agent_finish",
            output_data=finish.return_values,
            metadata={
                "run_id": str(run_id),
                "log": finish.log if hasattr(finish, "log") else None,
            },
        )

    # -------------------------------------------------------------------------
    # Trace Management
    # -------------------------------------------------------------------------

    def add_error_event(self, error: BaseException, context: str = "") -> None:
        """Manually add an error event to the trace."""
        self._add_event(
            event_type="error",
            name="runtime_error",
            error=str(error),
            metadata={
                "error_type": type(error).__name__,
                "context": context,
            },
        )

    def to_dict(self) -> dict:
        """Convert trace to dictionary format."""
        return {
            "run_id": self.run_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat() + "Z",
            "end_time": self._get_timestamp(),
            "duration_ms": round((time.time() - self.start_time) * 1000, 2),
            "total_events": len(self.events),
            "events": self.events,
            "metadata": {
                "trace_version": "1.0",
                "captured_by": "agent_autopsy",
            },
        }

    def save(self, path: Path | None = None) -> Path:
        """
        Save trace to JSON file.

        Args:
            path: Optional explicit path. If not provided, uses trace_dir config.

        Returns:
            Path where trace was saved.
        """
        if path is None:
            # Ensure trace directory exists
            self.config.trace_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{self.run_id}.json"
            path = self.config.trace_dir / filename

        # Atomic: a killed process must never leave a truncated trace.
        atomic_write_json(path, self.to_dict())

        return path


def start_trace(
    run_id: str | None = None,
    config: TraceConfig | None = None,
) -> tuple[TraceSaver, str]:
    """
    Start a new trace capture.

    Args:
        run_id: Optional run ID. If not provided, generates a UUID.
        config: Optional trace configuration. If not provided, loads from env.

    Returns:
        Tuple of (TraceSaver handler, run_id)
    """
    config = config or get_trace_config()

    if not config.enabled:
        # Return a disabled handler that won't capture anything
        handler = TraceSaver(run_id=run_id, config=config)
        return handler, handler.run_id

    handler = TraceSaver(run_id=run_id, config=config)
    return handler, handler.run_id


def end_trace(handler: TraceSaver, path: Path | None = None) -> Path | None:
    """
    End trace capture and save to disk.

    Args:
        handler: The TraceSaver handler from start_trace()
        path: Optional explicit path. If not provided, uses trace_dir config.

    Returns:
        Path where trace was saved, or None if tracing disabled.
    """
    if not handler.config.enabled:
        return None

    saved_path = handler.save(path)
    print(f"Trace saved: {saved_path}")
    return saved_path
