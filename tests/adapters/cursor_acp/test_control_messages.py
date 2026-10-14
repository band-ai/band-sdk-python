"""The ``/cursor`` room control commands."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from acp.schema import PermissionOption

from band.adapters.cursor_acp import (
    CursorACPAdapter,
    CursorACPAdapterConfig,
)
from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from band.integrations.acp.types import ACPToolCall
from tests.adapters.cursor_acp.kit import (
    DecisionTools,
    cursor_turn,
    pending_tokens,
    room_message,
)


class TestCursorACPAdapterControlMessages:
    @pytest.mark.asyncio
    async def test_bare_cursor_lists_pending_decisions(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage, SimpleNamespace(content="/cursor", sender_id="user-1")
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == "Pending Cursor decisions: none"

    @pytest.mark.asyncio
    async def test_cursor_decisions_lists_pending_decisions(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content="/cursor decisions", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == "Pending Cursor decisions: none"

    @pytest.mark.asyncio
    async def test_a_two_word_command_shows_usage(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                SimpleNamespace(content="/cursor accept", sender_id="user-1"),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == (
            "Use `/cursor decisions` to list pending Cursor decisions."
        )

    @pytest.mark.asyncio
    async def test_a_structurally_invalid_decision_command_is_rejected(self) -> None:
        tools = DecisionTools()
        adapter = CursorACPAdapter()
        pending = asyncio.create_task(
            adapter._resolve_plan(
                cursor_turn(adapter, "room-1", tools, "user-1", "session-1"),
                {"plan": "Plan"},
            )
        )
        await tools.prompt_sent.wait()
        token = pending_tokens(adapter)[0]

        handled = await adapter._handle_control_message(
            cast(
                PlatformMessage,
                # "select" is a permission verb, not a plan verb.
                SimpleNamespace(
                    content=f"/cursor select {token} allow-once", sender_id="user-1"
                ),
            ),
            tools,
            "room-1",
        )

        assert handled is True
        assert tools.messages[-1] == (
            f"That command is not valid for Cursor plan decision `{token}`."
        )
        assert not pending.done()
        adapter._cancel_all_decisions()
        await pending

    @pytest.mark.asyncio
    async def test_manual_permission_denies_silently_with_no_matching_active_turn(
        self,
    ) -> None:
        """No active turn for this room/session means there is no room to
        relay the decision to; it must deny without sending anything."""
        adapter = CursorACPAdapter()
        request = ACPPermissionRequest(
            room_id="room-1",
            session_id="session-1",
            tool_call=ACPToolCall("call-1", "shell", {}),
            options=(
                PermissionOption(
                    optionId="allow-once", name="Allow", kind="allow_once"
                ),
            ),
        )

        result = await adapter._resolve_cursor_permission(request)

        assert result is None

    @pytest.mark.asyncio
    async def test_exceeding_max_pending_decisions_evicts_the_rooms_oldest(
        self,
    ) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(max_pending_decisions=1))
        first_tools = DecisionTools()
        second_tools = DecisionTools()

        first = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(adapter, "room-1", first_tools, "user-1", "session-1"),
                prompt="Plan {token}",
            )
        )
        await first_tools.prompt_sent.wait()
        second = asyncio.create_task(
            adapter._wait_for_decision(
                kind="plan",
                turn=cursor_turn(
                    adapter, "room-1", second_tools, "user-1", "session-1"
                ),
                prompt="Plan {token}",
            )
        )
        await second_tools.prompt_sent.wait()

        assert await first is None
        assert len(pending_tokens(adapter)) == 1
        adapter._cancel_all_decisions()
        await second

    @pytest.mark.asyncio
    async def test_a_full_room_never_evicts_another_rooms_decision(self) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(max_pending_decisions=1))
        rooms = ("room-1", "room-2")
        tools = {room: DecisionTools() for room in rooms}
        waits = [
            asyncio.create_task(
                adapter._wait_for_decision(
                    kind="plan",
                    turn=cursor_turn(
                        adapter, room, tools[room], "user-1", f"session-{room}"
                    ),
                    prompt="Plan {token}",
                )
            )
            for room in rooms
        ]
        for room in rooms:
            await tools[room].prompt_sent.wait()
        [first_prompt] = tools["room-1"].messages
        token = first_prompt.removeprefix("Plan ")

        await adapter._handle_control_message(
            room_message(f"{ROOM_COMMAND} {CursorCommandWord.ACCEPT} {token}"),
            tools["room-1"],
            "room-1",
        )

        assert await waits[0] == {"outcome": {"outcome": "accepted"}}
        adapter._cancel_all_decisions()
        assert await waits[1] is None
