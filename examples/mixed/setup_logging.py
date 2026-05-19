"""Shared logging configuration for mixed examples."""

from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure concise logging for the mixed example suite."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("thenvoi").setLevel(level)
    logging.getLogger("thenvoi_crewai_agent").setLevel(level)
