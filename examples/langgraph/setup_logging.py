"""Shared logging configuration for examples."""

import logging


def setup_logging(level=logging.INFO):
    """Configure logging to show only band logs, hiding noisy dependencies."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("band").setLevel(level)
