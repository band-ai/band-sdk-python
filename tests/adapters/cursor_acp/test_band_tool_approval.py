"""Which tool permissions reach the room under each Cursor approval policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    ToolCallUpdate,
)

from band.core.types import Capability
from band.integrations.acp.permissions import ALLOW_ALWAYS_KIND, ALLOW_ONCE_KIND
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME
from band.runtime.tools.types import BandTool
from tests.adapters.cursor_acp.kit import (
    CursorRoom,
    permission_requests,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class TestBandToolApproval:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
    @pytest.mark.parametrize(
        "title",
        [
            pytest.param("band-band_store_memory: band_store_memory", id="memory"),
            pytest.param(
                "band-band_send_message: band_send_message",
                id="reply-with-custom-name-collision",
            ),
            pytest.param("band-echo: echo", id="custom-tool"),
        ],
    )
    async def test_registered_band_tools_are_approved_without_asking_the_room(
        self,
        cursor_approval_room: Callable[[str, str], Awaitable[CursorRoom]],
        approval_mode: str,
        title: str,
    ) -> None:
        room = await cursor_approval_room(title, approval_mode)
        tools = await room.send("run the tool")
        await room.turns_finished()

        assert room.agent.approved is True
        assert permission_requests(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("approval_mode", "approved", "room_requests"),
        [
            ("manual", False, 1),
            ("auto_accept", True, 0),
            ("auto_decline", False, 0),
        ],
    )
    @pytest.mark.parametrize(
        "title",
        [
            pytest.param(
                "other-band_send_message: band_send_message", id="other-server"
            ),
            pytest.param(
                "band-band_store_memory: band_send_message", id="mismatched-identity"
            ),
            pytest.param("shell", id="native-tool"),
            pytest.param("shell: shell", id="native-display-title"),
            pytest.param("band-unknown: unknown", id="unregistered-tool"),
        ],
    )
    async def test_other_tools_follow_cursor_approval_policy(
        self,
        cursor_approval_room: Callable[[str, str], Awaitable[CursorRoom]],
        approval_mode: str,
        title: str,
        approved: bool,
        room_requests: int,
    ) -> None:
        room = await cursor_approval_room(title, approval_mode)
        tools = await room.send("run the tool")
        await room.turns_finished()

        assert room.agent.approved is approved
        assert len(permission_requests(tools)) == room_requests

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("approval_mode", "persistent", "approved", "room_requests"),
        [
            ("manual", False, True, 0),
            ("auto_accept", False, True, 0),
            ("auto_decline", False, True, 0),
            ("manual", True, False, 1),
            ("auto_accept", True, True, 0),
            ("auto_decline", True, False, 0),
        ],
    )
    async def test_raw_input_band_permission_obeys_grant_duration(
        self,
        cursor_room: Callable[..., Awaitable[CursorRoom]],
        approval_mode: str,
        persistent: bool,
        approved: bool,
        room_requests: int,
    ) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def ask_band_permission(peer: FakeACPAgent, session_id: str) -> None:
            response = await peer.ask_permission(
                session_id,
                ToolCallUpdate(
                    toolCallId="reply",
                    title="band-band_store_memory: band_store_memory"
                    if persistent
                    else "Reply to room",
                    rawInput=None
                    if persistent
                    else {
                        "providerIdentifier": BAND_MCP_SERVER_NAME,
                        "toolName": BandTool.STORE_MEMORY,
                        "args": {},
                    },
                ),
                [
                    PermissionOption(
                        optionId="grant",
                        name="Allow",
                        kind=ALLOW_ALWAYS_KIND if persistent else ALLOW_ONCE_KIND,
                    )
                ],
            )
            peer.approved = isinstance(response.outcome, AllowedOutcome)
            await peer.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={"content": "finished", "mentions": ["@user-1"]},
            )

        room = await cursor_room(
            agent,
            approval_mode=approval_mode,
            capabilities={Capability.MEMORY},
            decision_timeout_s=0.1,
        )
        tools = await room.send("reply")
        await room.turns_finished()

        assert agent.approved is approved
        assert len(permission_requests(tools)) == room_requests

    @pytest.mark.asyncio
    @pytest.mark.parametrize("approval_mode", ["manual", "auto_decline"])
    async def test_registered_native_title_uses_configured_permission_policy(
        self,
        cursor_approval_room: Callable[[str, str], Awaitable[CursorRoom]],
        approval_mode: str,
    ) -> None:
        room = await cursor_approval_room("echo", approval_mode)
        tools = await room.send("run native echo")
        await room.turns_finished()
        assert room.agent.approved is False
        assert len(permission_requests(tools)) == (
            1 if approval_mode == "manual" else 0
        )

    @pytest.mark.asyncio
    async def test_foreign_mcp_identity_cannot_use_a_band_display_title(
        self,
        cursor_room: Callable[..., Awaitable[CursorRoom]],
    ) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def ask_foreign_permission(peer: FakeACPAgent, session_id: str) -> None:
            response = await peer.ask_permission(
                session_id,
                ToolCallUpdate(
                    toolCallId="foreign",
                    title="band-band_send_message: band_send_message",
                    rawInput={
                        "providerIdentifier": "foreign",
                        "toolName": BandTool.SEND_MESSAGE,
                        "args": {},
                    },
                ),
                [
                    PermissionOption(
                        optionId="once", name="Allow once", kind=ALLOW_ONCE_KIND
                    )
                ],
            )
            peer.approved = isinstance(response.outcome, AllowedOutcome)
            await peer.call_mcp_tool(
                session_id=session_id,
                server=BAND_MCP_SERVER_NAME,
                tool_name=BandTool.SEND_MESSAGE,
                arguments={"content": "finished", "mentions": ["@user-1"]},
            )

        room = await cursor_room(agent, approval_mode="auto_decline")
        await room.send("run")
        await room.turns_finished()
        assert agent.approved is False
