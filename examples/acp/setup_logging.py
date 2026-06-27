"""Logging setup for ACP examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the example."""
    configure_logging(
        level=level,
        root_level=level,
        extra_loggers={
            "httpcore": logging.WARNING,
            "httpx": logging.WARNING,
        },
    )
