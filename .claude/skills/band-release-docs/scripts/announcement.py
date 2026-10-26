# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Format a release announcement for Slack or Discord from ``release-notes.json``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path

from releasedata import Category, Item, ReleaseNotes, in_categories
from textsafety import discord_text, slack_text

logger = logging.getLogger(__name__)


class Target(StrEnum):
    SLACK_WEBHOOK = "slack-webhook"
    DISCORD = "discord"


# Emphasis per destination: Slack mrkdwn uses one asterisk, Discord two.
TARGET_MARKERS = {Target.SLACK_WEBHOOK: "*", Target.DISCORD: "**"}
TARGET_TEXT: dict[Target, Callable[[str], str]] = {
    Target.SLACK_WEBHOOK: slack_text,
    Target.DISCORD: discord_text,
}
# Discord fires the ping only when the content holds it and postdiscord.py
# allows it through allowed_mentions.
DISCORD_MENTION = "@everyone"
DISCORD_MAX_CHARS = 2000
TEST_DISCLAIMER = "TEST — please ignore"
FIXES = {Category.FIXED, Category.SECURITY}
SECTIONS = (("What's new", set(Category) - FIXES), ("Fixes", FIXES))


def bullet(item: Item, target: Target) -> str:
    mark, text = TARGET_MARKERS[target], TARGET_TEXT[target]
    return f"• {mark}{text(item.label)}{mark} — {text(item.sentence)}"


def opening(target: Target, *, test: bool) -> list[str]:
    """A test post opens with the disclaimer and pings no one; live Discord pings all."""
    if test:
        return [TEST_DISCLAIMER]
    return [DISCORD_MENTION] if target is Target.DISCORD else []


def compose(
    notes: ReleaseNotes, target: Target, *, test: bool, items: Sequence[Item]
) -> str:
    """Build the message from ``items``, in section order."""
    mark = TARGET_MARKERS[target]
    blocks = [*opening(target, test=test)]
    blocks.append(f"{mark}{notes.product.name} v{notes.version}{mark} is live!")
    for title, categories in SECTIONS:
        listed = in_categories(items, categories)
        if listed:
            lines = [bullet(item, target) for item in listed]
            blocks.append("\n".join([f"{mark}{title}:{mark}", *lines]))
    hidden = len(notes.items) - len(items)
    closing = f"…and {hidden} more — full notes: " if hidden else "Full notes: "
    blocks.append(closing + notes.release_url)
    return "\n\n".join(blocks)


def section_order(notes: ReleaseNotes) -> list[Item]:
    return [item for _, categories in SECTIONS for item in notes.items_in(categories)]


def format_announcement(
    notes: ReleaseNotes, target: Target, *, test: bool = False
) -> str:
    """Return the post; Discord keeps whole items up to its length limit."""
    ordered = section_order(notes)
    message = compose(notes, target, test=test, items=ordered)
    if target is not Target.DISCORD:
        return message
    while len(message) > DISCORD_MAX_CHARS and ordered:
        ordered = ordered[:-1]
        message = compose(notes, target, test=test, items=ordered)
    return message


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--target", type=Target, required=True, choices=list(Target))
    parser.add_argument(
        "--test", action="store_true", help="disclaimer, no channel ping"
    )
    args = parser.parse_args(argv)

    notes = ReleaseNotes.model_validate_json(args.notes.read_text(encoding="utf-8"))
    sys.stdout.write(format_announcement(notes, args.target, test=args.test) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
