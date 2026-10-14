"""A successful decline ends the native graph without another model request."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import PlatformMessage
from band.runtime.tools import BandTool
from tests.integrations.langgraph.declinekit import (
    DeclineFailure,
    DeclineTools,
    ScriptedModel,
)


class TestExplicitDecline:
    @pytest.fixture
    def message(self) -> PlatformMessage:
        return PlatformMessage(
            id="message",
            room_id="room-fake",
            content="FYI",
            sender_id="user",
            sender_type="User",
            sender_name="User",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

    @pytest.mark.parametrize(
        "failure",
        [None, *DeclineFailure],
    )
    async def test_only_a_successful_batch_is_terminal(
        self, message: PlatformMessage, failure: DeclineFailure | None
    ) -> None:
        calls = [
            {
                "name": BandTool.NO_REPLY,
                "args": {
                    "reason": 42
                    if failure == DeclineFailure.INVALID_ARGUMENTS
                    else "FYI"
                },
                "id": "decline",
            }
        ]
        if failure == DeclineFailure.FAILED_SIBLING:
            calls.append(
                {"name": BandTool.GET_PARTICIPANTS, "args": {}, "id": "sibling"}
            )
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=calls),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": BandTool.SEND_MESSAGE,
                                "args": {
                                    "content": "fresh reply",
                                    "mentions": [
                                        "00000000-0000-0000-0000-000000000001"
                                    ],
                                },
                                "id": "reply",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(llm=model)
        tools = DeclineTools(failure)
        await adapter.on_started("Bot", "Bot")
        await adapter.run_judged_turn(
            msg=message,
            tools=tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=tools.room_id,
        )
        assert model.requests == (1 if failure is None else 3)
        assert tools.turn.replied
        assert tools.replies == ([] if failure is None else ["fresh reply"])

    async def test_a_previous_decline_does_not_end_the_next_turn(
        self, message: PlatformMessage
    ) -> None:
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": BandTool.NO_REPLY,
                                "args": {"reason": "FYI"},
                                "id": "decline",
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": BandTool.SEND_MESSAGE,
                                "args": {
                                    "content": "fresh reply",
                                    "mentions": [
                                        "00000000-0000-0000-0000-000000000001"
                                    ],
                                },
                                "id": "reply",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(llm=model)
        await adapter.on_started("Bot", "Bot")
        for bootstrap in (True, False):
            tools = DeclineTools()
            await adapter.run_judged_turn(
                msg=message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=bootstrap,
                room_id=tools.room_id,
            )
        assert model.requests == 3
        assert tools.replies == ["fresh reply"]
