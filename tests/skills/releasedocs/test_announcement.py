"""Announcements group the release's items per destination and respect Discord's length limit."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from tests.skills.releasedocs.samples import (
    RECONNECT,
    RELEASE_URL,
    SEND_RETRIES,
    TOKEN_REDACTION,
    TOOL_PREFIX,
)


def release_items(make_item: Callable[..., object]) -> list[object]:
    return [
        make_item(*RECONNECT),
        make_item(*SEND_RETRIES),
        make_item(*TOKEN_REDACTION),
        make_item(*TOOL_PREFIX),
    ]


class TestAnnouncement:
    def test_slack_post_groups_items_with_single_asterisks(
        self,
        announcement: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
    ) -> None:
        notes = make_notes(release_items(make_item))
        assert announcement.format_announcement(
            notes, announcement.Target.SLACK_WEBHOOK
        ) == (
            "*Band Python SDK v6.1.0* is live!\n"
            "\n"
            "*What's new:*\n"
            "• *Send Retries* — Sends retry with backoff.\n"
            "• *Tool Prefix* — Tool names start with a prefix.\n"
            "\n"
            "*Fixes:*\n"
            "• *Reconnect* — Rooms rejoin after a drop.\n"
            "• *Token Redaction* — Keys never reach logs.\n"
            "\n"
            f"Full notes: {RELEASE_URL}"
        )

    def test_discord_post_pings_everyone_with_double_asterisks(
        self,
        announcement: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
    ) -> None:
        notes = make_notes([make_item(*SEND_RETRIES)])
        assert announcement.format_announcement(notes, announcement.Target.DISCORD) == (
            "@everyone\n"
            "\n"
            "**Band Python SDK v6.1.0** is live!\n"
            "\n"
            "**What's new:**\n"
            "• **Send Retries** — Sends retry with backoff.\n"
            "\n"
            f"Full notes: {RELEASE_URL}"
        )

    def test_test_post_opens_with_the_disclaimer_and_pings_no_one(
        self,
        announcement: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
    ) -> None:
        notes = make_notes([make_item(*RECONNECT)])
        assert announcement.format_announcement(
            notes, announcement.Target.DISCORD, test=True
        ) == (
            "TEST — please ignore\n"
            "\n"
            "**Band Python SDK v6.1.0** is live!\n"
            "\n"
            "**Fixes:**\n"
            "• **Reconnect** — Rooms rejoin after a drop.\n"
            "\n"
            f"Full notes: {RELEASE_URL}"
        )

    def test_model_text_cannot_ping_or_inject_markup(
        self,
        announcement: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
    ) -> None:
        notes = make_notes(
            [make_item("Added", "<!channel> Alerts", "Tell @here first.")]
        )
        slack = announcement.format_announcement(
            notes, announcement.Target.SLACK_WEBHOOK
        )
        discord = announcement.format_announcement(notes, announcement.Target.DISCORD)
        assert (
            slack.splitlines()[3] == "• *&lt;!channel&gt; Alerts* — Tell @here first."
        )
        assert discord.splitlines()[5] == "• **<!channel> Alerts** — Tell  first."

    def test_long_release_keeps_whole_items_within_discords_limit(
        self,
        announcement: ModuleType,
        make_item: Callable[..., object],
        make_notes: Callable[..., object],
    ) -> None:
        sentence = "x" * 150
        items = [
            make_item("Added", f"Feature {index:02}", sentence) for index in range(20)
        ]
        notes = make_notes(items)
        discord = announcement.format_announcement(notes, announcement.Target.DISCORD)
        slack = announcement.format_announcement(
            notes, announcement.Target.SLACK_WEBHOOK
        )
        kept = [line for line in discord.splitlines() if line.startswith("• ")]
        assert len(discord) <= announcement.DISCORD_MAX_CHARS
        assert kept == [
            f"• **Feature {index:02}** — {sentence}" for index in range(len(kept))
        ]
        assert discord.endswith(
            f"…and {20 - len(kept)} more — full notes: {RELEASE_URL}"
        )
        assert slack.count("• ") == 20
