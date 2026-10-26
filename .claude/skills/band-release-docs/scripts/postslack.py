# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3", "pydantic-settings>=2.16,<3"]
# ///
"""Post a preformatted announcement (stdin or ``--message-file``) to a Slack incoming webhook."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from webhooks import read_message, send

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/services/"


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    slack_webhook_url: SecretStr


def validate_webhook_url(url: str) -> None:
    if not url.startswith(SLACK_WEBHOOK_PREFIX):
        raise ValueError(f"SLACK_WEBHOOK_URL must start with {SLACK_WEBHOOK_PREFIX}")


def post_message(webhook_url: str, text: str) -> None:
    send("Slack", "POST", webhook_url, {"text": text})


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-file", type=Path, help="defaults to stdin")
    args = parser.parse_args(argv)

    try:
        webhook_url = SlackSettings().slack_webhook_url.get_secret_value()
        validate_webhook_url(webhook_url)
        post_message(webhook_url, read_message(args.message_file))
    except ValidationError:
        logger.error("error: SLACK_WEBHOOK_URL is required")
        return 1
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    logger.info("Posted to Slack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
