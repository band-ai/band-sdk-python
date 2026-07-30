"""Shared logging configuration for Slack examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies."""
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = LogSettings(**kwargs)
    settings.configure(extra_loggers={"slack_sdk": settings.log_level})
