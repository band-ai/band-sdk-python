"""Append values to a GitHub Actions step-outputs file."""

from __future__ import annotations

from pathlib import Path


def append_outputs(path: Path | None, **values: str) -> None:
    """Append ``name=value`` lines; a missing path means no workflow is listening."""
    if path is None:
        return
    with path.open("a", encoding="utf-8") as outputs:
        outputs.writelines(f"{name}={value}\n" for name, value in values.items())
