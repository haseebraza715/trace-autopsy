"""
Advanced fix suggestion generation.

Produces trace-tailored fix suggestions and (when source-location metadata is
present in the trace and a code root is provided) applyable unified diffs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.output.code_context import (
    SourceLocation,
    find_source_locations_for_events,
    insert_lines_at,
)
from src.preanalysis import PreAnalysisBundle
from src.schema import Trace


# Per-pattern guard snippets used when building unified diffs.
_LOOP_GUARD_SNIPPET = """\
# agent-autopsy: loop guard
state['_aa_iter'] = state.get('_aa_iter', 0) + 1
if state['_aa_iter'] > MAX_ITERATIONS:
    raise RuntimeError('agent-autopsy: max iterations exceeded')
"""

_RETRY_BACKOFF_SNIPPET = """\
# agent-autopsy: bounded retry with backoff
import time, random
for _attempt in range(MAX_RETRIES):
    try:
        break
    except Exception:
        time.sleep((2 ** _attempt) + random.uniform(0, 0.5))
else:
    raise RuntimeError('agent-autopsy: retry budget exhausted')
"""

_TOOL_VALIDATOR_SNIPPET = """\
# agent-autopsy: tool allow-list validator
if tool_name not in ALLOWED_TOOLS:
    raise ValueError(f'agent-autopsy: unknown tool {tool_name!r}; allowed: {sorted(ALLOWED_TOOLS)}')
"""


_PATTERN_PATCH_TEMPLATES: dict[str, str] = {
    "infinite_loop": _LOOP_GUARD_SNIPPET,
    "retry_storm": _RETRY_BACKOFF_SNIPPET,
    "hallucinated_tool": _TOOL_VALIDATOR_SNIPPET,
}


@dataclass
class FixSuggestion:
    """A generated fix suggestion."""

    title: str
    category: str
    rationale: str
    patch_snippet: str
    event_ids: list[int]
    source_locations: list[SourceLocation] = field(default_factory=list)
    patch_diff: str | None = None  # populated when code_root is provided
    pattern_type: str | None = None  # used to look up patch templates


class FixSuggestionGenerator:
    """Generate actionable, trace-aware fix suggestions."""

    def __init__(
        self,
        trace: Trace,
        preanalysis: PreAnalysisBundle,
        *,
        code_root: Path | None = None,
    ):
        self.trace = trace
        self.preanalysis = preanalysis
        self.code_root = code_root

    def generate(self) -> list[FixSuggestion]:
        suggestions: list[FixSuggestion] = []
        signals = self.preanalysis.signals
        available_tools = self.trace.env.tools_available

        for signal in signals:
            if signal.type == "infinite_loop":
                node = self._guess_loop_node(signal.event_ids)
                suggestions.append(
                    FixSuggestion(
                        title="Add max-iteration guard to looping node",
                        category="code",
                        rationale="Identical tool calls repeated consecutively indicate missing termination criteria.",
                        patch_snippet=(
                            f"# candidate node: {node}\n"
                            + _LOOP_GUARD_SNIPPET
                        ),
                        event_ids=signal.event_ids,
                        pattern_type="infinite_loop",
                    )
                )
            elif signal.type == "retry_storm":
                suggestions.append(
                    FixSuggestion(
                        title="Add bounded retry with exponential backoff",
                        category="ops",
                        rationale="Many retries in a short window indicate missing backoff and retry budget.",
                        patch_snippet=_RETRY_BACKOFF_SNIPPET,
                        event_ids=signal.event_ids,
                        pattern_type="retry_storm",
                    )
                )
            elif signal.type == "hallucinated_tool":
                tools_text = ", ".join(available_tools) if available_tools else "<declare tools>"
                suggestions.append(
                    FixSuggestion(
                        title="Harden system prompt with explicit tool allow-list",
                        category="prompt",
                        rationale="Tool hallucination can be reduced with explicit allowed tool names and policy.",
                        patch_snippet=(
                            "You may only call these tools:\n"
                            f"{tools_text}\n"
                            "If required functionality is unavailable, ask for guidance instead of inventing tools.\n"
                        ),
                        event_ids=signal.event_ids,
                        pattern_type="hallucinated_tool",
                    )
                )
            elif signal.type == "error_cascade":
                root_tool = self._guess_root_error_tool(signal.event_ids)
                suggestions.append(
                    FixSuggestion(
                        title="Wrap failing tool calls with local error boundary",
                        category="code",
                        rationale="Error cascades indicate one failure propagates without containment.",
                        patch_snippet=(
                            f"def safe_{root_tool}(**kwargs):\n"
                            "    try:\n"
                            f"        return {root_tool}(**kwargs)\n"
                            "    except Exception as exc:\n"
                            "        return {'ok': False, 'error': str(exc), 'retryable': False}\n"
                        ),
                        event_ids=signal.event_ids,
                    )
                )
            elif signal.type == "context_overflow":
                suggestions.append(
                    FixSuggestion(
                        title="Add adaptive context windowing",
                        category="ops",
                        rationale="Context overflow indicates prompt assembly exceeds model limits.",
                        patch_snippet=(
                            "def build_context(history, max_tokens):\n"
                            "    while estimate_tokens(history) > max_tokens:\n"
                            "        history = summarize_oldest_chunk(history)\n"
                            "    return history\n"
                        ),
                        event_ids=signal.event_ids,
                    )
                )

        # Deduplicate by title + category
        seen: set[tuple[str, str]] = set()
        deduped: list[FixSuggestion] = []
        for suggestion in suggestions:
            key = (suggestion.title, suggestion.category)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(suggestion)

        # Enrich with source locations + (optionally) unified diffs.
        for suggestion in deduped:
            suggestion.source_locations = find_source_locations_for_events(
                self.trace, suggestion.event_ids
            )
            if self.code_root is not None and suggestion.pattern_type in _PATTERN_PATCH_TEMPLATES:
                suggestion.patch_diff = self._build_diff(suggestion)

        return deduped

    def _build_diff(self, suggestion: FixSuggestion) -> str | None:
        """
        Build a unified diff for one suggestion. Returns None when no source
        location resolves, when the pattern has no template, or when patch
        construction otherwise can't proceed.
        """
        template = _PATTERN_PATCH_TEMPLATES.get(suggestion.pattern_type or "")
        if template is None or self.code_root is None:
            return None
        for loc in suggestion.source_locations:
            resolved = loc.resolve(self.code_root)
            if resolved is None:
                continue
            line = loc.line_number or 1
            try:
                rel = str(resolved.relative_to(self.code_root))
            except ValueError:
                rel = resolved.name
            return insert_lines_at(resolved, line, template, rel_path=rel)
        return None

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialize suggestions to dictionaries."""
        return [
            {
                "title": suggestion.title,
                "category": suggestion.category,
                "rationale": suggestion.rationale,
                "patch_snippet": suggestion.patch_snippet,
                "event_ids": suggestion.event_ids,
                "source_locations": [
                    {
                        "file_path": loc.file_path,
                        "line_number": loc.line_number,
                        "function_name": loc.function_name,
                    }
                    for loc in suggestion.source_locations
                ],
                "patch_diff": suggestion.patch_diff,
                "pattern_type": suggestion.pattern_type,
            }
            for suggestion in self.generate()
        ]

    def write_patches(self, output_dir: Path) -> list[Path]:
        """
        Write unified-diff patches into `output_dir/<pattern>.patch`.

        Only suggestions with a non-None `patch_diff` are written. Returns the
        list of paths actually created.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for s in self.generate():
            if not s.patch_diff or not s.pattern_type:
                continue
            path = output_dir / f"{s.pattern_type}.patch"
            path.write_text(s.patch_diff)
            written.append(path)
        return written

    def _guess_loop_node(self, event_ids: list[int]) -> str:
        for event_id in event_ids:
            event = self.trace.get_event(event_id)
            if event and event.name:
                return event.name
        return "router_or_loop_node"

    def _guess_root_error_tool(self, event_ids: list[int]) -> str:
        for event_id in event_ids:
            event = self.trace.get_event(event_id)
            if event and event.name:
                return event.name.replace("-", "_")
        return "tool_call"
