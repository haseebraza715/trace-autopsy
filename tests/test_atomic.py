"""Atomic write guarantees: no partial reads, no orphan temps, no clobber."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_autopsy.utils.atomic import atomic_write_json, atomic_write_text


def test_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_json(target, {"ok": True, "n": 1})
    assert json.loads(target.read_text()) == {"ok": True, "n": 1}
    atomic_write_text(target.with_suffix(".txt"), "hello")
    assert target.with_suffix(".txt").read_text() == "hello"


def test_failed_write_leaves_original_and_no_tmp(tmp_path: Path) -> None:
    target = tmp_path / "keep.json"
    atomic_write_json(target, {"version": 1})

    class Boom:
        def write(self, *_):
            raise OSError("disk full")

    import agent_autopsy.utils.atomic as mod

    def failing(fdopen):
        fdopen.return_value = Boom()

    try:
        real_fdopen = mod.os.fdopen
        mod.os.fdopen = lambda *a, **k: Boom()
        try:
            atomic_write_json(target, {"version": 2})
        except OSError:
            pass
        else:
            raise AssertionError("expected OSError to propagate")
    finally:
        mod.os.fdopen = real_fdopen

    assert json.loads(target.read_text()) == {"version": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_writers_always_leave_valid_file(tmp_path: Path) -> None:
    target = tmp_path / "shared.json"
    atomic_write_json(target, {"writer": -1})
    errors = []
    start = threading.Barrier(8)

    def writer(i: int) -> None:
        start.wait()
        try:
            for n in range(25):
                atomic_write_json(target, {"writer": i, "n": n})
                data = json.loads(target.read_text())
                assert "writer" in data and "n" in data
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert list(tmp_path.glob("*.tmp")) == []
