"""Create persisted session paths for adapter recovery tests."""

from __future__ import annotations

from pathlib import Path

from band.adapters.omp import (
    OMP_SESSION_DIRECTORY,
)


def native_session_file(workspace: Path) -> Path:
    directory = workspace / OMP_SESSION_DIRECTORY
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "saved.jsonl"
    path.write_text("{}", encoding="utf-8")
    return path
