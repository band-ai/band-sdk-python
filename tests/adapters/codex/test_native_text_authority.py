"""Codex adapter: native text authority."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.runtime.tools import BandTool
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    parked_on_approval,
    run_codex_turn,
)
from tests.adapters.codexturns import (
    final_text,
    tool_call_request,
    turn_completed,
)


class TestNativeTextAuthority:
    @pytest.mark.asyncio
    async def test_a_tool_reply_suppresses_the_final_text(self) -> None:
        turn = await run_codex_turn(
            events=[
                tool_call_request(
                    1, BandTool.SEND_MESSAGE, {"content": "Hi", "mentions": ["@a"]}
                ),
                final_text("Hi, again."),
                turn_completed(),
            ]
        )

        assert turn.tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_a_policy_notification_never_stands_in_for_the_answer(
        self,
    ) -> None:
        turn = await run_codex_turn(
            events=[
                *parked_on_approval(final_text("Tests pass.")),
                turn_completed(),
            ],
            config=CodexAdapterConfig(
                approval_mode="auto_accept", approval_text_notifications=True
            ),
        )

        assert turn.tools.chat == [
            "Approval requested (command: a). Policy decision: accept."
        ]
        assert not turn.tools.turn.complete
        assert [e["content"] for e in events_of_type(turn.tools, "thought")] == [
            "Codex approval request handled automatically (accept).",
            "Tests pass.",
        ]
