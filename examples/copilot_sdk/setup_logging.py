"""Shared logging configuration for Copilot SDK examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies.

    Level comes from an explicit argument or ``BAND_LOG_LEVEL`` (default INFO).
    """
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    LogSettings(**kwargs).configure()
