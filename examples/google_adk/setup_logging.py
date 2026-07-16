"""Shared logging configuration for examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to show only band logs, hiding noisy dependencies."""
    configure_logging(level)
