"""Shared logging configuration for examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show band + band_crewai_agent logs, hiding other noisy dependencies."""
    settings = LogSettings.create(log_level=level)
    settings.configure(extra_loggers={"band_crewai_agent": settings.log_level})
