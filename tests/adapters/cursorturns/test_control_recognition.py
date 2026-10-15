"""Recognizing a Cursor command reads the message alone and changes nothing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.adapters.cursor_acp import CursorACPAdapter
from band.agent import Agent
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    active_turn,
    busy_provider,
    command,
    cursor_adapter,
    decision_token,
    occupy,
    terminated,
)
from tests.conftest import make_message_event
from tests.integrations.acp.acp_toolkit.harness import live_message
from tests.runtime.batching.kit import room_context

RECOGNIZED = [
    "/cursor",
    "/cursor decisions",
    "/CURSOR deny abc",
    "@[[7f3a]] /cursor select abc allow-1",
    "/cursor select",
    "/cursor frobnicate abc",
]
ORDINARY = [
    "",
    "cursor decisions",
    "/cursors decisions",
    "please run /cursor decisions",
    "what does /cursor mean?",
]


class TestCursorControlRecognition:
    @pytest.mark.parametrize("content", RECOGNIZED)
    def test_a_command_is_a_control_even_when_it_is_invalid(self, content: str) -> None:
        assert CursorACPAdapter().is_control_message(live_message(content))

    @pytest.mark.parametrize("content", ORDINARY)
    def test_anything_else_is_an_ordinary_input(self, content: str) -> None:
        assert not CursorACPAdapter().is_control_message(live_message(content))

    @pytest.mark.parametrize(
        ("content", "is_control"),
        [("@[[alice]] /cursor select abc allow-1", True), ("hello", False)],
    )
    def test_the_runtime_classifies_a_platform_delivery_with_the_same_parser(
        self, content: str, is_control: bool
    ) -> None:
        agent = Agent(runtime=MagicMock(agent_id="agent-1"), adapter=CursorACPAdapter())

        classified = agent._is_control(
            room_context(), make_message_event(content=content)
        )

        assert classified is is_control

    async def test_a_pending_decision_does_not_turn_free_text_into_a_control(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)

        assert not cursor_adapter(session).is_control_message(
            live_message("yes, allow it")
        )

    async def test_recognizing_a_command_does_not_resolve_or_claim_its_decision(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("approval")
        session = await open_cursor(provider.agent)
        await occupy(session, provider)
        turn = active_turn(session)
        resolve = f"/cursor select {decision_token(session)} allow-1"

        assert cursor_adapter(session).is_control_message(live_message(resolve))
        consumed, _ = await command(session, resolve, sender_id="alice")
        await terminated(turn)

        assert (consumed, provider.agent.approved) == (True, True)
