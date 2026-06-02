"""Shared logging configuration for examples."""

import logging


def setup_logging(level=logging.INFO):
    """Configure logging to show only band logs, hiding noisy dependencies.

    Args:
        level: Log level for band namespace (default INFO)
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.getLogger("band").setLevel(level)
    # Also enable logging for the parlant adapter module
    logging.getLogger("band_parlant_agent").setLevel(level)
