"""Logging setup for Codex examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for examples."""
    configure_logging(
        level=level,
        root_level=level,
        stream="stdout",
        extra_loggers={
            "websockets": logging.WARNING,
            "httpx": logging.WARNING,
        },
    )
