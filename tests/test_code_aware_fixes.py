"""
Tests for src/output/code_context.py and the diff-emitting path of
src/output/fix_generator.py.

The end-to-end test writes a synthetic agent file, generates a patch from a
trace whose event metadata points at it, applies the patch with `git apply`,
and asserts the file now contains the expected guard.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from src.output.code_context import (
    SourceLocation,
    build_unified_diff,
    find_source_location,
    find_source_locations_for_events,
    insert_lines_at,
)
from src.output.fix_generator import FixSuggestionGenerator
from src.preanalysis import PatternResult, PatternType, PreAnalysisBundle, Severity
from src.preanalysis.suspects import Hypothesis, Signal
from src.schema import (
    EnvironmentInfo,
    EventType,
    Trace,
    TraceEvent,
    TraceStatus,
)


def _make_trace(events: list[TraceEvent]) -> Trace:
    return Trace(
        run_id="test_code_aware",
        timestamp_start=datetime(2026, 1, 1),
        status=TraceStatus.FAILED,
        env=EnvironmentInfo(agent_framework="test"),
        events=events,
    )


class TestFindSourceLocation:
    def test_otel_code_keys(self):
        ev = TraceEvent(
            event_id=1,
            type=EventType.TOOL_CALL,
            metadata={
                "code.filepath": "/agent/loops.py",
                "code.lineno": 42,
                "code.function": "router",
            },
        )
        loc = find_source_location(ev)
        assert loc == SourceLocation(file_path="/agent/loops.py", line_number=42, function_name="router")

    def test_langchain_dialect(self):
        ev = TraceEvent(
            event_id=1,
            type=EventType.TOOL_CALL,
            metadata={"source_file": "agent/loops.py", "lineno": "10"},
        )
        loc = find_source_location(ev)
        assert loc is not None
        assert loc.file_path == "agent/loops.py"
        assert loc.line_number == 10

    def test_no_metadata_returns_none(self):
        ev = TraceEvent(event_id=1, type=EventType.TOOL_CALL, metadata={})
        assert find_source_location(ev) is None

    def test_dedup_by_file_and_line(self):
        events = [
            TraceEvent(event_id=1, type=EventType.TOOL_CALL,
                       metadata={"code.filepath": "a.py", "code.lineno": 5}),
            TraceEvent(event_id=2, type=EventType.TOOL_CALL,
                       metadata={"code.filepath": "a.py", "code.lineno": 5}),  # dup
            TraceEvent(event_id=3, type=EventType.TOOL_CALL,
                       metadata={"code.filepath": "a.py", "code.lineno": 9}),
        ]
        trace = _make_trace(events)
        locs = find_source_locations_for_events(trace, [1, 2, 3])
        assert len(locs) == 2
        assert {(l.file_path, l.line_number) for l in locs} == {("a.py", 5), ("a.py", 9)}


class TestSourceLocationResolve:
    def test_resolves_relative_to_code_root(self, tmp_path: Path):
        target = tmp_path / "agent" / "loops.py"
        target.parent.mkdir()
        target.write_text("pass\n")
        loc = SourceLocation(file_path="agent/loops.py")
        assert loc.resolve(tmp_path) == target.resolve()

    def test_resolves_by_basename_when_path_doesnt_match(self, tmp_path: Path):
        nested = tmp_path / "src" / "sub" / "loops.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("pass\n")
        # Metadata says just the bare filename
        loc = SourceLocation(file_path="loops.py")
        assert loc.resolve(tmp_path) == nested.resolve()

    def test_returns_none_when_missing(self, tmp_path: Path):
        loc = SourceLocation(file_path="nope.py")
        assert loc.resolve(tmp_path) is None


class TestUnifiedDiff:
    def test_insert_lines_at_produces_valid_diff(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("def go():\n    return 1\n")
        diff = insert_lines_at(f, 2, "x = 1\n", rel_path="a.py")
        assert diff.startswith("--- a/a.py")
        assert "+++ b/a.py" in diff
        assert "+    x = 1" in diff  # indent matched to surrounding code

    def test_build_unified_diff_empty_when_no_change(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("hello\n")
        diff = build_unified_diff(f, ["hello"])
        assert diff == "" or diff.strip() == ""


@pytest.fixture
def loop_trace_with_source(tmp_path: Path) -> tuple[Trace, Path]:
    """A trace with a loop pattern whose events point at a real file."""
    code_root = tmp_path
    target = code_root / "router.py"
    target.write_text(
        "MAX_ITERATIONS = 10\n"
        "\n"
        "def step(state, tool):\n"
        "    return tool(**state)\n"
    )
    events = [
        TraceEvent(
            event_id=i,
            type=EventType.TOOL_CALL,
            name="search",
            input={"q": "weather"},
            metadata={"code.filepath": "router.py", "code.lineno": 4},
        )
        for i in range(5)
    ]
    return _make_trace(events), code_root


def _make_preanalysis_with_loop_signal(event_ids: list[int]) -> PreAnalysisBundle:
    sig = Signal(
        type="infinite_loop",
        severity="critical",
        evidence="repeated calls",
        event_ids=event_ids,
    )
    hyp = Hypothesis(
        description="Missing exit condition in router",
        confidence=0.9,
        supporting_events=event_ids,
        category="code",
    )
    return PreAnalysisBundle(signals=[sig], hypotheses=[hyp], summary="loop")


class TestFixGeneratorWithCodeRoot:
    def test_emits_patch_diff_for_infinite_loop(self, loop_trace_with_source):
        trace, code_root = loop_trace_with_source
        pre = _make_preanalysis_with_loop_signal([0, 1, 2, 3, 4])
        gen = FixSuggestionGenerator(trace, pre, code_root=code_root)
        suggestions = gen.generate()
        loop_sugg = [s for s in suggestions if s.pattern_type == "infinite_loop"]
        assert len(loop_sugg) == 1
        assert loop_sugg[0].patch_diff is not None
        assert "agent-autopsy: loop guard" in loop_sugg[0].patch_diff
        assert loop_sugg[0].source_locations  # at least one resolved location

    def test_no_diff_without_code_root(self, loop_trace_with_source):
        trace, _ = loop_trace_with_source
        pre = _make_preanalysis_with_loop_signal([0, 1, 2, 3, 4])
        gen = FixSuggestionGenerator(trace, pre, code_root=None)
        suggestions = gen.generate()
        assert all(s.patch_diff is None for s in suggestions)

    def test_no_diff_when_source_metadata_missing(self, tmp_path: Path):
        events = [
            TraceEvent(event_id=i, type=EventType.TOOL_CALL, name="search",
                       metadata={})  # no source-location hints
            for i in range(3)
        ]
        trace = _make_trace(events)
        pre = _make_preanalysis_with_loop_signal([0, 1, 2])
        gen = FixSuggestionGenerator(trace, pre, code_root=tmp_path)
        suggestions = gen.generate()
        assert all(s.patch_diff is None for s in suggestions)

    def test_write_patches_creates_file(self, loop_trace_with_source, tmp_path: Path):
        trace, code_root = loop_trace_with_source
        pre = _make_preanalysis_with_loop_signal([0, 1, 2, 3, 4])
        gen = FixSuggestionGenerator(trace, pre, code_root=code_root)
        out = tmp_path / "patches"
        written = gen.write_patches(out)
        assert (out / "infinite_loop.patch").exists()
        assert any(p.name == "infinite_loop.patch" for p in written)


@pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not available for patch-apply round trip",
)
class TestPatchAppliesCleanly:
    """End-to-end: generated patch applies via `git apply`, file gains the guard."""

    def test_loop_guard_patch_applies(self, loop_trace_with_source, tmp_path: Path):
        trace, code_root = loop_trace_with_source
        pre = _make_preanalysis_with_loop_signal([0, 1, 2, 3, 4])
        gen = FixSuggestionGenerator(trace, pre, code_root=code_root)
        out = tmp_path / "patches"
        gen.write_patches(out)
        patch_path = out / "infinite_loop.patch"
        assert patch_path.is_file()

        # `git apply` works on bare paths from cwd; the patch's a/router.py
        # path matches a file in code_root.
        check = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=code_root,
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, f"git apply --check failed: {check.stderr}"

        applied = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=code_root,
            capture_output=True,
            text=True,
        )
        assert applied.returncode == 0, f"git apply failed: {applied.stderr}"

        new_content = (code_root / "router.py").read_text()
        assert "agent-autopsy: loop guard" in new_content
