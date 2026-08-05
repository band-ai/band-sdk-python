"""Shared logging configuration for Claude SDK examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show band + band_claude_sdk_agent + session_manager logs, hiding other noisy dependencies."""
    settings = LogSettings.create(log_level=level)
    settings.configure(
        extra_loggers={
            "band_claude_sdk_agent": settings.log_level,
            "session_manager": settings.log_level,
        }
    )
