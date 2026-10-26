# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3", "pydantic-settings>=2.16,<3"]
# ///
"""Post a preformatted announcement to a Discord webhook, or delete one by id.

Posting prints the new message's id, which ``--delete`` takes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from webhooks import read_message, send

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)
# Release posts ping the channel; webhooks parse only user mentions by default.
# `parse` also keeps user and role pings suppressed.
CHANNEL_MENTIONS = {"parse": ["everyone"]}
NO_MENTIONS: dict[str, list[str]] = {"parse": []}


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    discord_webhook_url: SecretStr


def validate_webhook_url(url: str) -> None:
    if not url.startswith(DISCORD_WEBHOOK_PREFIXES):
        raise ValueError(
            f"DISCORD_WEBHOOK_URL must start with {DISCORD_WEBHOOK_PREFIXES[0]}"
        )


def post_message(webhook_url: str, text: str, *, mentions: bool = True) -> str:
    """Post ``text`` and return the created message's id."""
    allowed = CHANNEL_MENTIONS if mentions else NO_MENTIONS
    query = urllib.parse.urlencode({"wait": "true"})
    body = send(
        "Discord",
        "POST",
        f"{webhook_url}?{query}",
        {"content": text, "allowed_mentions": allowed},
    )
    return str(json.loads(body)["id"])


def delete_message(webhook_url: str, message_id: str) -> None:
    send(
        "Discord", "DELETE", f"{webhook_url}/messages/{urllib.parse.quote(message_id)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-file", type=Path, help="defaults to stdin")
    parser.add_argument("--no-mentions", action="store_true", help="ping no one")
    parser.add_argument(
        "--delete", metavar="MESSAGE_ID", help="delete this message instead"
    )
    args = parser.parse_args(argv)

    try:
        webhook_url = DiscordSettings().discord_webhook_url.get_secret_value()
        validate_webhook_url(webhook_url)
        if args.delete:
            delete_message(webhook_url, args.delete)
            logger.info("Deleted Discord message %s", args.delete)
            return 0
        message_id = post_message(
            webhook_url, read_message(args.message_file), mentions=not args.no_mentions
        )
    except ValidationError:
        logger.error("error: DISCORD_WEBHOOK_URL is required")
        return 1
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    sys.stdout.write(message_id + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
