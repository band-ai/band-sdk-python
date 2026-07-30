"""Logging setup for Codex examples."""

from __future__ import annotations

from band import LogLevel, LogSettings, chatty_logger_levels
from band.logging_config import LoggingStyle, LogStream


class CodexLogSettings(LogSettings):
    """JSON on stdout; root follows the application level."""

    log_console_style: LoggingStyle = LoggingStyle.JSON
    log_stream: LogStream = LogStream.STDOUT


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging for examples."""
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = CodexLogSettings(**kwargs)
    settings.for_application().configure(
        extra_loggers={
            **chatty_logger_levels("WARNING"),
            "websockets": "WARNING",
        },
    )
