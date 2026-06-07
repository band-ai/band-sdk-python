"""History converter for the Slack bridge adapter.

Recovers the Slack thread identity for a room from its history, so the
bridge can resume routing Slack events into the right Band room after
a restart.

History is converted per-room (the platform fetches and converts a single
room's history at a time), so this converter only needs to surface the
binding for the current room. The room_id itself is supplied by the
caller; this converter just answers "is this a Slack-bridged room, and
if so which Slack thread does it mirror?".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from band.integrations.slack.types import SlackSessionState

logger = logging.getLogger(__name__)


class SlackHistoryConverter(HistoryConverter["SlackSessionState"]):
    """Extracts the Slack thread binding from a room's history.

    Scans for a ``task`` event carrying the ``slack_app_slug`` +
    ``slack_channel_id`` + ``slack_thread_ts`` metadata that
    :py:meth:`SlackAdapter._emit_context_event` writes when a Slack-
    bridged room is first created. Returns the most recent such binding,
    or ``None`` if the room has no Slack bootstrap event.
    """

    def convert(self, raw: list[dict[str, Any]]) -> SlackSessionState:
        """Build session state from a single room's history.

        Args:
            raw: Platform history dicts (``format_history_for_llm`` shape).

        Returns:
            ``SlackSessionState`` with ``binding`` set when the history
            contains a Slack bootstrap task event, otherwise the empty
            default state.
        """
        from band.integrations.slack.types import (
            SlackRoomBinding,
            SlackSessionState,
        )

        binding: SlackRoomBinding | None = None
        for msg in raw:
            if msg.get("message_type") != "task":
                continue
            metadata = msg.get("metadata") or {}
            app_slug = metadata.get("slack_app_slug")
            channel = metadata.get("slack_channel_id")
            thread_ts = metadata.get("slack_thread_ts")
            if not (app_slug and channel and thread_ts):
                continue
            # Last bootstrap event wins. Multiple shouldn't happen in
            # practice, but if it does we trust the most recent context.
            binding = SlackRoomBinding(
                app_slug=app_slug,
                channel=channel,
                thread_ts=thread_ts,
            )

        if binding is not None:
            logger.debug(
                "Recovered Slack binding from history: app=%s channel=%s thread_ts=%s",
                binding.app_slug,
                binding.channel,
                binding.thread_ts,
            )

        return SlackSessionState(binding=binding)
