"""Source-only contract specification tooling."""

from __future__ import annotations

if not __debug__:
    raise SystemExit(
        "Contract specification tools refuse to run under python -O: assertion gates must execute"
    )
