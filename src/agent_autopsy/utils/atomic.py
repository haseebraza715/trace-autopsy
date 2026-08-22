"""Atomic file writes for artifacts that must never be left truncated."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, write: Any) -> None:
    """Write via a unique temp sibling and rename, so readers never see a
    partial file and concurrent writers cannot clobber each other's temp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            write(f)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        finally:
            raise


def atomic_write_text(path: Path, text: str) -> None:
    _atomic_write(path, lambda f: f.write(text))


def atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, lambda f: json.dump(payload, f, indent=2, default=str))
