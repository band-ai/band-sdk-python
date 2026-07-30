"""Shared logging configuration for Claude SDK examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies."""
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = LogSettings(**kwargs)
    settings.configure(
        extra_loggers={
            "band_claude_sdk_agent": settings.log_level,
            "session_manager": settings.log_level,
        }
    )
