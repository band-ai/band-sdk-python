"""Kit: read the step-outputs file a script appended to."""

from __future__ import annotations

from pathlib import Path


def read_outputs(path: Path) -> dict[str, str]:
    """The ``name=value`` lines in ``path``; empty when nothing was written."""
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines)
