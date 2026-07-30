"""Shared logging configuration for examples."""

from __future__ import annotations

from band import LogLevel, LogSettings


def setup_logging(level: LogLevel | None = None, a2a_debug: bool = False) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies.

    Args:
        level: Log level for band package (default: INFO via BAND_LOG_LEVEL)
        a2a_debug: If True, enable DEBUG logging for A2A adapter to trace
            context_id and session rehydration
    """
    kwargs: dict[str, LogLevel] = {}
    if level is not None:
        kwargs["log_level"] = level
    settings = LogSettings(**kwargs)
    extra = {"band.integrations.a2a": "DEBUG"} if a2a_debug else None
    settings.configure(extra_loggers=extra)
