"""Logging setup for OpenCode examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for examples."""
    configure_logging(
        level=level,
        stream="stdout",
        root_level=level,
        extra_loggers={"httpx": logging.WARNING},
    )
