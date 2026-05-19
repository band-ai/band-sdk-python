"""Shared logging configuration for Claude SDK examples."""

import logging


def setup_logging(level=logging.INFO):
    """Configure logging to show only thenvoi logs, hiding noisy dependencies."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("thenvoi").setLevel(level)
    # Also enable logging for the claude_sdk adapter module
    logging.getLogger("thenvoi_claude_sdk_agent").setLevel(level)
    logging.getLogger("session_manager").setLevel(level)
