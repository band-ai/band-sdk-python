"""Shared logging configuration for Slack examples."""

from __future__ import annotations

import logging

from band import configure_logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging to show band + slack-sdk logs, mute noisy deps."""
    configure_logging(
        level,
        extra_loggers={"slack_sdk": level},
    )
