"""Logging setup for OpenCode examples."""

from __future__ import annotations

from band import LogLevel, LogSettings, chatty_logger_levels
from band.logging_config import LogStream


class OpencodeLogSettings(LogSettings):
    """Stdout stream; root follows the application level."""

    log_stream: LogStream = LogStream.STDOUT


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging for examples."""
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = OpencodeLogSettings(**kwargs)
    settings.for_application().configure(
        extra_loggers=chatty_logger_levels("WARNING"),
    )
