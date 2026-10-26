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

from webhooks import WebhookSettings, read_message, send, webhook_url

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_PREFIXES = ("https://hooks.slack.com/services/",)


def post_message(webhook_url: str, text: str) -> None:
    send("Slack", "POST", webhook_url, {"text": text})


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-file", type=Path, help="defaults to stdin")
    args = parser.parse_args(argv)

    try:
        url = webhook_url(
            "SLACK_WEBHOOK_URL",
            WebhookSettings().slack_webhook_url,
            SLACK_WEBHOOK_PREFIXES,
        )
        post_message(url, read_message(args.message_file))
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    logger.info("Posted to Slack")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
