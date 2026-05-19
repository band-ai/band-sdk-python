"""Shared logging configuration for examples."""

import logging


def setup_logging(level=logging.INFO, a2a_debug: bool = False):
    """Configure logging to show only thenvoi logs, hiding noisy dependencies.

    Args:
        level: Log level for thenvoi package (default: INFO)
        a2a_debug: If True, enable DEBUG logging for A2A adapter to trace
            context_id and session rehydration
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("thenvoi").setLevel(level)
    if a2a_debug:
        logging.getLogger("thenvoi.integrations.a2a").setLevel(logging.DEBUG)
