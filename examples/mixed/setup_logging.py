"""Shared logging configuration for mixed examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure concise logging for the mixed example suite."""
    configure_logging(
        level,
        extra_loggers={"band_crewai_agent": level},
    )
