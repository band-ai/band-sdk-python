"""Shared logging configuration for examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show band logs, hiding other noisy dependencies."""
    settings = LogSettings.create(log_level=level)
    settings.configure()
