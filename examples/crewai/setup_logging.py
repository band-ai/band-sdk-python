"""Shared logging configuration for examples."""

import logging


def setup_logging(level=logging.INFO):
    """Configure logging to show only thenvoi logs, hiding noisy dependencies."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("thenvoi").setLevel(level)
    # Also enable logging for the crewai adapter module
    logging.getLogger("thenvoi_crewai_agent").setLevel(level)
