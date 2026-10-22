"""Shared data and helpers for specification checks."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from contracts.spec.tools import oldformguard as guard

OLD = """state new
  lead -> reviewer: request() => reviewing
state reviewing
  reviewer -> lead: review() => done
end done
"""

HEAD = """state new
  lead -> reviewer: request() awaits reviewing
    => review() & done
     | @5m & stalled
state retrying
  lead -> reviewer: request() => reviewing
end done
end stalled
"""


def verdicts(source: str) -> list[tuple[int, str]]:
    return [(line, verdict) for line, verdict, _, _ in guard.check(source.split("\n"))]


def scan_files(files: dict[str, str]) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            path = Path(tmp) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return guard.scan(tmp, list(files))
