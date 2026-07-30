"""Shared logging configuration for examples."""

from __future__ import annotations

from band import LogLevel, LogSettings
from band.logging_config import LoggingStyle


class ParlantLogSettings(LogSettings):
    """Rich console output for Parlant examples."""

    log_console_style: LoggingStyle = LoggingStyle.RICH


def setup_logging(level: LogLevel | None = None) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies.

    Args:
        level: Log level for band namespace (default INFO via BAND_LOG_LEVEL)
    """
    settings = ParlantLogSettings.create(log_level=level)
    settings.configure(extra_loggers={"band_parlant_agent": settings.log_level})
