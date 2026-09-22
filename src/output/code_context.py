"""
Locate source code for a trace event so fix suggestions can be emitted as
applyable unified diffs rather than illustrative snippets.

Trace events sometimes carry source-location metadata: OpenTelemetry standardizes
`code.filepath`, `code.lineno`, `code.function`. LangChain/LangGraph traces often
include `source_file` / `file` / `lineno`. When such fields are present we can
generate a real patch; otherwise the artifact generator falls back to the
illustrative snippet.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.schema import Trace, TraceEvent


# Metadata key dialects we accept as source-location hints, ordered by
# specificity. The first non-None value for each field wins.
_FILEPATH_KEYS = (
    "code.filepath",
    "code.file_path",
    "source_file",
    "file_path",
    "filename",
    "file",
)
_LINENO_KEYS = (
    "code.lineno",
    "code.line_no",
    "line_number",
    "lineno",
    "line",
)
_FUNCTION_KEYS = (
    "code.function",
    "function_name",
    "function",
    "callable",
)


@dataclass(frozen=True)
class SourceLocation:
    """A pointer to a specific spot in the user's source code."""

    file_path: str
    line_number: int | None = None
    function_name: str | None = None

    def resolve(self, code_root: Path) -> Path | None:
        """Resolve to an absolute path under `code_root` if the file exists."""
        # Try as-is first (handles already-absolute paths in metadata).
        candidate = Path(self.file_path)
        if candidate.is_absolute() and candidate.is_file():
            return candidate
        # Then try relative to code_root.
        rooted = (code_root / self.file_path).resolve()
        if rooted.is_file():
            return rooted
        # Finally try matching by basename — useful when metadata stores only
        # a filename and the user's repo has it nested somewhere.
        basename = candidate.name
        for match in code_root.rglob(basename):
            if match.is_file():
                return match
        return None


def _first_string(metadata: dict, keys: Iterable[str]) -> str | None:
    for k in keys:
        v = metadata.get(k)
        if v is not None:
            return str(v)
    return None


def _first_int(metadata: dict, keys: Iterable[str]) -> int | None:
    for k in keys:
        v = metadata.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def find_source_location(event: TraceEvent) -> SourceLocation | None:
    """Extract source-location hints from one event's metadata."""
    md = event.metadata or {}
    file_path = _first_string(md, _FILEPATH_KEYS)
    if not file_path:
        return None
    return SourceLocation(
        file_path=file_path,
        line_number=_first_int(md, _LINENO_KEYS),
        function_name=_first_string(md, _FUNCTION_KEYS),
    )


def find_source_locations_for_events(
    trace: Trace,
    event_ids: Iterable[int],
) -> list[SourceLocation]:
    """Collect every source location found across the given events."""
    out: list[SourceLocation] = []
    seen: set[tuple[str, int | None]] = set()
    for eid in event_ids:
        ev = trace.get_event(eid)
        if ev is None:
            continue
        loc = find_source_location(ev)
        if loc is None:
            continue
        key = (loc.file_path, loc.line_number)
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def build_unified_diff(
    file_path: Path,
    new_lines: list[str],
    *,
    rel_path: str | None = None,
) -> str:
    """
    Produce a unified diff converting the file at `file_path` into `new_lines`.

    Returns an empty string when the file is unchanged.
    """
    original = file_path.read_text().splitlines(keepends=False)
    a_label = f"a/{rel_path or file_path.name}"
    b_label = f"b/{rel_path or file_path.name}"
    diff_lines = list(difflib.unified_diff(
        original,
        new_lines,
        fromfile=a_label,
        tofile=b_label,
        lineterm="",
    ))
    if not diff_lines:
        return ""
    # `git apply` requires a trailing newline on the patch.
    return "\n".join(diff_lines) + "\n"


def insert_lines_at(
    file_path: Path,
    line_number: int,
    snippet: str,
    *,
    rel_path: str | None = None,
) -> str:
    """
    Insert `snippet` immediately before `line_number` (1-indexed) in the file.

    Returns a unified diff. Indentation of the inserted snippet is matched to
    the surrounding code's indent prefix (best-effort).
    """
    original = file_path.read_text().splitlines(keepends=False)
    insert_at = max(0, min(line_number - 1, len(original)))

    # Match the indentation of the line we're inserting before, if available.
    indent = ""
    if insert_at < len(original):
        target = original[insert_at]
        indent = target[: len(target) - len(target.lstrip())]

    snippet_lines = [
        (indent + line if line else line) for line in snippet.splitlines()
    ]
    new_lines = original[:insert_at] + snippet_lines + original[insert_at:]
    return build_unified_diff(file_path, new_lines, rel_path=rel_path)
