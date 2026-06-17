from __future__ import annotations

import re
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "thenvoi"
_BANNED_RUNTIME_PATTERNS = {
    "issue ID": re.compile(r"\bINT-\d+\b"),
    "conformance seam": re.compile(r"conformance seam", re.IGNORECASE),
    "conformance validation": re.compile(r"conformance validation", re.IGNORECASE),
    "baseline conformance": re.compile(r"baseline conformance", re.IGNORECASE),
    "scorecard": re.compile(r"scorecard", re.IGNORECASE),
    "test-contract": re.compile(r"test-contract", re.IGNORECASE),
}


def test_runtime_source_does_not_carry_harness_provenance() -> None:
    hits: list[str] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for label, pattern in _BANNED_RUNTIME_PATTERNS.items():
            if pattern.search(text):
                hits.append(f"{path.relative_to(_SOURCE_ROOT)}: {label}")

    assert not hits, (
        "Runtime source should describe product behavior, not harness provenance:\n"
        + "\n".join(hits)
    )
