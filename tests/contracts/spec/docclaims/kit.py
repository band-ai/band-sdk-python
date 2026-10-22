"""Shared data and helpers for specification checks."""

from __future__ import annotations

import re
from pathlib import Path

from tests.paths import CONTRACTS_ROOT as ROOT

SKILL = ROOT / "skills/multi-agent-contracts"


def prose(path: Path) -> str:
    """Return the file with every whitespace run folded, so wrapped phrases still match."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def docs(*patterns: str) -> list[Path]:
    return sorted(
        {p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()}
    )
