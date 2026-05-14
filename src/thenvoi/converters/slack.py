"""History converter for the Slack bridge adapter.

Repopulates ``SlackSessionState.thread_to_room`` from platform history so
the bridge can resume routing existing Slack threads after restart. See
Step 8 in INT-461 for the implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thenvoi.core.protocols import HistoryConverter

if TYPE_CHECKING:
    from thenvoi.integrations.slack.types import SlackSessionState


class SlackHistoryConverter(HistoryConverter["SlackSessionState"]):
    """Extracts ``thread_to_room`` mappings from platform history."""

    def convert(self, raw: list[dict[str, Any]]) -> SlackSessionState:
        """Build session state from history events.

        Step 8 will look for ``task`` events whose metadata carries
        ``slack_channel_id`` + ``slack_thread_ts`` and rebuild
        ``thread_to_room`` from them. For now, returns an empty state so
        the bridge starts cleanly without rehydration.
        """
        from thenvoi.integrations.slack.types import SlackSessionState

        return SlackSessionState()
