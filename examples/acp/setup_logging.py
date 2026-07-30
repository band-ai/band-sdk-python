"""Logging setup for ACP examples."""

from __future__ import annotations

from band import LogLevel, LogSettings, chatty_logger_levels


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging for the example."""
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = LogSettings(**kwargs)
    settings.for_application().configure(
        extra_loggers=chatty_logger_levels("WARNING"),
    )
