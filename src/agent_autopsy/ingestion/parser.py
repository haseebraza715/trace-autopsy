"""
Base parser interface and factory for trace parsing.

This module provides the abstract base class for trace parsers
and a factory function to select the appropriate parser based on format.
"""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from agent_autopsy.errors import ParseError, PluginError, SchemaValidationError
from agent_autopsy.plugins import get_plugin_manager
from agent_autopsy.schema import Trace

logger = logging.getLogger(__name__)


def first_non_none(data: dict[str, Any], *keys: str) -> Any:
    """Select aliases without discarding recorded empty or zero values."""
    return next((data[key] for key in keys if data.get(key) is not None), None)


class TraceParser(ABC):
    """Abstract base class for trace parsers."""

    @abstractmethod
    def can_parse(self, data: dict[str, Any]) -> bool:
        """Check if this parser can handle the given data."""
        pass

    @abstractmethod
    def parse(self, data: dict[str, Any]) -> Trace:
        """Parse the data into a normalized Trace."""
        pass

    @classmethod
    def detect_format(cls, data: dict[str, Any]) -> str:
        """Detect the format of the trace data."""
        plugin_manager = get_plugin_manager()
        for plugin in plugin_manager.parsers:
            try:
                if plugin.can_parse(data):
                    return f"plugin:{plugin.name}"
            except PluginError:
                raise
            except Exception:
                logger.exception(
                    "Plugin parser %r failed during format detection; skipping plugin",
                    getattr(plugin, "name", type(plugin).__name__),
                )
                continue

        # OpenTelemetry detection (check first to avoid false positives)
        if "resourceSpans" in data or "traceId" in data:
            return "opentelemetry"

        # LangGraph detection
        if "thread_id" in data or "checkpoint" in data:
            return "langgraph"
        if "runs" in data and isinstance(data["runs"], list):
            # Distinguish LangChain runs from generic LangGraph run arrays.
            if any(
                isinstance(run, dict) and run.get("run_type")
                for run in data["runs"]
            ):
                return "langchain"
            return "langgraph"

        # LangChain detection
        if "run_type" in data and data.get("run_type") in ["chain", "llm", "tool"]:
            return "langchain"
        if "callbacks" in data:
            return "langchain"

        return "generic"


def parse_trace_file(file_path: str | Path) -> Trace:
    """
    Parse a trace file and return a normalized Trace.

    This is the main entry point for trace parsing.
    It auto-detects the format and uses the appropriate parser.

    Args:
        file_path: Path to the trace JSON file

    Returns:
        Normalized Trace object
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ParseError(f"Could not read trace file {path} as UTF-8 text: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON in trace file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ParseError(f"Trace file {path} must contain a JSON object at the root")

    return parse_trace_data(data)


def parse_trace_data(data: dict[str, Any]) -> Trace:
    """
    Parse raw trace JSON data and return a normalized Trace.

    Args:
        data: Parsed trace payload as dictionary

    Returns:
        Normalized Trace object
    """
    from .formats.generic import GenericJSONParser
    from .formats.langchain import LangChainParser
    from .formats.langgraph import LangGraphParser
    from .formats.opentelemetry import OpenTelemetryParser

    if not isinstance(data, dict):
        raise ParseError("Trace data must be a dictionary")

    # First let parser plugins try.
    plugin_manager = get_plugin_manager()
    for plugin in plugin_manager.parsers:
        pname = getattr(plugin, "name", type(plugin).__name__)
        try:
            if plugin.can_parse(data):
                try:
                    return plugin.parse(data)
                except PydanticValidationError as exc:
                    raise SchemaValidationError(
                        f"Plugin parser {pname!r} produced invalid trace schema: {exc}"
                    ) from exc
                except ParseError:
                    raise
                except Exception as exc:
                    raise PluginError(f"Plugin parser {pname!r} failed while parsing trace") from exc
        except PluginError:
            raise
        except SchemaValidationError:
            raise
        except ParseError:
            raise
        except Exception:
            logger.exception("Plugin parser %r failed during can_parse; skipping plugin", pname)
            continue

    # Detect built-in format and select parser
    format_type = TraceParser.detect_format(data)

    parsers: dict[str, TraceParser] = {
        "langgraph": LangGraphParser(),
        "langchain": LangChainParser(),
        "opentelemetry": OpenTelemetryParser(),
        "generic": GenericJSONParser(),
    }

    parser = parsers.get(format_type, GenericJSONParser())

    if not parser.can_parse(data):
        # Fallback to generic parser
        parser = GenericJSONParser()

    try:
        return parser.parse(data)
    except PydanticValidationError as exc:
        raise SchemaValidationError(f"Trace failed schema validation ({format_type}): {exc}") from exc
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Failed to parse trace as format {format_type!r}: {exc}") from exc
