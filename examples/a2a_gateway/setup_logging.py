"""Logging setup for A2A Gateway examples."""

from __future__ import annotations

from band import LogLevel, LogSettings, chatty_logger_levels


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging for the example."""
    settings = LogSettings.create(log_level=level)
    settings.for_application().configure(
        extra_loggers={
            **chatty_logger_levels("WARNING"),
            "uvicorn": "WARNING",
        },
    )
