"""Detached failure reports cannot mark a replacement turn."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import band_sdk_core
import pytest

from band.runtime.tools import AgentTools
from tests.runtime.turnledger.kit import ALICE


class TestDetachedFailureIsolation:
    @pytest.mark.parametrize("judged", [True, False], ids=["judged", "contact-hub"])
    async def test_a_detached_report_never_marks_the_contexts_next_message(
        self, mock_rest_client: Any, judged: bool
    ) -> None:
        """A released turn reports after its delivery settled, while the context
        may already be processing a later message, judged or not."""
        ctx = MagicMock(
            participants=[ALICE],
            agent_id="agent-1",
            hub_room_id=None,
            current_scope=None,
        )
        ctx.link.rest = mock_rest_client
        tools = AgentTools.from_context(ctx)
        tools.turn.judged = judged
        tools.turn.detach()

        await tools.send_failure(band_sdk_core.AgentFailure("codex", "timed out"))

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_awaited_once()
        ctx.note_turn_failure_reported.assert_not_called()
