"""Real ACP permissions for the Cursor manual-approval completion fixture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp.schema import PermissionOption, ToolCallUpdate

from band.adapters.cursor_acp import CursorACPAdapterConfig
from band.integrations.acp.client_runtime import ALLOW_ALWAYS_KIND, ALLOW_ONCE_KIND
from band.integrations.acp.cursor import (
    PERMISSION_REQUESTED_TEMPLATE,
    ROOM_COMMAND,
    CursorCommandWord,
)
from band.runtime.tools import BandTool
from band.testing import failure_reports
from tests.adapters.test_cursor_acp_adapter import CursorRoom, decision_token, said
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    AgentSetup,
    cursor_approval_adapter,
)
from tests.e2e.baseline.smoke.samples.cursorapproval import CursorApprovalAdapter
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import started_acp_adapter

REPLY_ARGS = {"content": "completed", "mentions": ["@alice"]}
ONCE_ID = "reply-this-time"
ALWAYS_ID = "remember-reply"


def reply_invocation(
    *, server: str = "band", tool: str = BandTool.SEND_MESSAGE, args: Any = None
) -> dict[str, Any]:
    return {
        "providerIdentifier": server,
        "toolName": tool,
        "args": REPLY_ARGS if args is None else args,
    }


def options(*, once: bool = True) -> list[PermissionOption]:
    return [
        PermissionOption(kind=ALLOW_ALWAYS_KIND, name="Allow", option_id=ALWAYS_ID),
        *(
            [PermissionOption(kind=ALLOW_ONCE_KIND, name="Allow", option_id=ONCE_ID)]
            if once
            else []
        ),
    ]


def decisions(agent: FakeACPAgent) -> list[str | None]:
    return [
        getattr(response.outcome, "option_id", None)
        for response in agent.permission_responses
    ]


@asynccontextmanager
async def approval_room(
    agent: FakeACPAgent, *, registered: bool = True
) -> AsyncIterator[CursorRoom]:
    adapter = CursorApprovalAdapter(
        CursorACPAdapterConfig(),
        include_tools=None if registered else [BandTool.NO_REPLY],
    )
    async with started_acp_adapter(adapter, agent):
        yield CursorRoom(adapter, agent)


async def test_completion_reply_selects_once_and_delivers_through_mcp(
    tmp_path: Path,
) -> None:
    agent = (
        FakeACPAgent()
        .will_ask_permission(raw_input=reply_invocation(), options=options())
        .will_call_mcp_tool("reply", BandTool.SEND_MESSAGE, arguments=REPLY_ARGS)
    )
    adapter = cursor_approval_adapter(
        BaselineSettings(), AgentSetup(workdir=tmp_path, wait_timeout_s=10)
    )
    async with started_acp_adapter(adapter, agent):
        room = CursorRoom(adapter, agent)
        tools = await room.send("reply")
        await room.turns_finished()

    assert decisions(agent) == [ONCE_ID]
    assert said(tools) == [REPLY_ARGS["content"]]
    assert tools.turn.replied
    assert failure_reports(tools) == []


@pytest.mark.parametrize(
    ("invocation", "once", "registered"),
    [
        (reply_invocation(server="foreign"), True, True),
        (reply_invocation(tool=BandTool.SEND_MESSAGE.upper()), True, True),
        (reply_invocation(tool=f"{BandTool.SEND_MESSAGE}_extra"), True, True),
        (reply_invocation(tool="custom_tool"), True, True),
        ({"command": "write a file"}, True, True),
        (reply_invocation(args={"content": "completed"}), True, True),
        (reply_invocation(args={"content": " ", "mentions": ["@alice"]}), True, True),
        (reply_invocation(), False, True),
        (reply_invocation(), True, False),
    ],
    ids=[
        "foreign-server",
        "case-sensitive",
        "lookalike",
        "custom-tool",
        "shell",
        "malformed",
        "blank-reply",
        "no-once-option",
        "unregistered",
    ],
)
async def test_other_permissions_still_require_the_room_decision(
    invocation: dict[str, Any], once: bool, registered: bool
) -> None:
    agent = FakeACPAgent().will_ask_permission(
        raw_input=invocation, options=options(once=once)
    )
    async with approval_room(agent, registered=registered) as room:
        tools = await room.send("ask")
        [prompt] = said(tools)
        await room.send(
            f"{ROOM_COMMAND} {CursorCommandWord.DENY} {decision_token(prompt)}"
        )
        await room.turns_finished()

    assert decisions(agent) == [None]


@pytest.mark.parametrize(
    "mismatch", ["room", "session", "missing-session", "missing-turn"]
)
async def test_completion_cannot_grant_outside_the_active_turn(mismatch: str) -> None:
    agent = FakeACPAgent()
    async with approval_room(agent) as room:

        @agent.on_prompt
        async def request_outside_turn(peer: FakeACPAgent, session_id: str) -> None:
            turn = room.adapter._active_turn
            assert turn is not None
            match mismatch:
                case "room":
                    turn.room_id = "another-room"
                case "session":
                    session_id = "another-session"
                case "missing-session":
                    turn.session_id = None
                case "missing-turn":
                    room.adapter._active_turn = None
            try:
                await peer.ask_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id="reply", raw_input=reply_invocation()),
                    options(),
                )
            finally:
                room.adapter._active_turn = turn
            await peer.call_mcp_tool(
                session_id=peer.session_ids()[0],
                server="band",
                tool_name=BandTool.NO_REPLY,
                arguments={"reason": "permission cancelled"},
            )

        tools = await room.send("ask")
        await room.turns_finished()

    assert decisions(agent) == [None]
    assert said(tools) == []
    assert failure_reports(tools) == []


async def test_native_commands_on_both_sides_of_completion_remain_manual(
    tmp_path: Path,
) -> None:
    agent = FakeACPAgent()
    async with approval_room(agent) as room:

        @agent.on_prompt
        async def commands_and_reply(peer: FakeACPAgent, session_id: str) -> None:
            for index in range(2):
                response = await peer.ask_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id=f"shell-{index}", title="shell"),
                    options(),
                )
                if getattr(response.outcome, "option_id", None) == ONCE_ID:
                    (tmp_path / f"effect-{index}").write_text("approved")
                if not index:
                    await peer.ask_permission(
                        session_id,
                        ToolCallUpdate(
                            tool_call_id="reply", raw_input=reply_invocation()
                        ),
                        options(),
                    )
                    await peer.call_mcp_tool(
                        session_id=session_id,
                        server="band",
                        tool_name=BandTool.SEND_MESSAGE,
                        arguments=REPLY_ARGS,
                    )

        tools = await room.send("run two commands")
        [first] = said(tools)
        assert list(tmp_path.iterdir()) == []
        await room.send(
            f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {decision_token(first)} {ONCE_ID}"
        )
        await tools.until_said(
            PERMISSION_REQUESTED_TEMPLATE.split("{tool}")[0], times=2
        )
        second = said(tools)[-1]
        await room.send(
            f"{ROOM_COMMAND} {CursorCommandWord.DENY} {decision_token(second)}"
        )
        await room.turns_finished()

    assert decisions(agent) == [ONCE_ID, ONCE_ID, None]
    assert (tmp_path / "effect-0").read_text() == "approved"
    assert not (tmp_path / "effect-1").exists()
    assert failure_reports(tools) == []
