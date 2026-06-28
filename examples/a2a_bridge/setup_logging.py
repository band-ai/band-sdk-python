"""Shared logging configuration for examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO, a2a_debug: bool = False) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies.

    Args:
        level: Log level for band package (default: INFO)
        a2a_debug: If True, enable DEBUG logging for A2A adapter to trace
            context_id and session rehydration
    """
    extra_loggers = {"band.integrations.a2a": logging.DEBUG} if a2a_debug else None
    configure_logging(level, extra_loggers=extra_loggers)
