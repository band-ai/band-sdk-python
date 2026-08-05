"""Shared logging configuration for Slack examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show band + slack_sdk logs, hiding other noisy dependencies."""
    settings = LogSettings.create(log_level=level)
    settings.configure(extra_loggers={"slack_sdk": settings.log_level})
