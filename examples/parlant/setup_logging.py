"""Shared logging configuration for examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies.

    Args:
        level: Log level for band namespace (default INFO)
    """
    configure_logging(
        level,
        style="rich",
        extra_loggers={"band_parlant_agent": level},
    )
