"""Append values to a GitHub Actions step-outputs file."""

from __future__ import annotations

from pathlib import Path


def output_value(value: str | bool) -> str:
    return str(value).lower() if isinstance(value, bool) else value


def append_outputs(path: Path | None, **values: str | bool) -> None:
    """Append ``name=value`` lines; a missing path means no workflow is listening.

    Booleans are written as the ``true``/``false`` the workflow compares against.
    """
    if path is None:
        return
    with path.open("a", encoding="utf-8") as outputs:
        outputs.writelines(
            f"{name}={output_value(value)}\n" for name, value in values.items()
        )
