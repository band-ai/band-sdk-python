"""Shared logging configuration for Slack examples."""

import logging


def setup_logging(level=logging.INFO):
    """Configure logging to show band + slack-sdk logs, mute noisy deps."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("band").setLevel(level)
    # Surface Slack SDK retries / Socket Mode connect events.
    logging.getLogger("slack_sdk").setLevel(level)
