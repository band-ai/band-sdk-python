"""Logging setup for A2A Gateway examples."""

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
            "uvicorn": logging.WARNING,
        },
    )
