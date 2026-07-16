"""Shared logging configuration for Copilot SDK examples."""

from __future__ import annotations

import os

from band import LogLevel, configure_logging


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies."""
    if level is None:
        # Tolerate LOG_LEVEL= (empty) and numeric forms like LOG_LEVEL=20;
        # configure_logging rejects both as level-name strings.
        raw = os.environ.get("LOG_LEVEL") or "INFO"
        level = int(raw) if raw.isdecimal() else raw
    configure_logging(level)
