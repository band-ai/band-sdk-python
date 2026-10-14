"""A room leave retires answers and session approval policy together."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexAdapter, CodexCommand
from band.integrations.codex.types import CodexApprovalMethod
from tests.adapters.codex.kit import (
    ACCEPTED,
    PATIENT_APPROVAL_S,
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
    requested_approval_token,
    residency_of,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    await_released_turn,
    event_request,
    turn_completed,
)


class TestApprovalRetirement:
    @pytest.mark.parametrize(
        "approval_id", [None, "reused"], ids=["request-id", "approval-id"]
    )
    @pytest.mark.parametrize(
        "restart", [False, True], ids=["leave-rejoin", "stop-restart"]
    )
    async def test_a_reused_native_id_does_not_reuse_the_left_rooms_answer_token(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        approval_id: str | None,
        restart: bool,
    ) -> None:
        params = {"command": "npm test"}
        if approval_id is not None:
            params["approvalId"] = approval_id
        old, new = (
            FakeCodexClient(
                events=[
                    event_request(10, CodexApprovalMethod.COMMAND_EXECUTION, params),
                    turn_completed(),
                ]
            )
            for _ in range(2)
        )
        adapter = await started_codex(
            old, new, approval_mode="manual", approval_wait_timeout_s=PATIENT_APPROVAL_S
        )
        old_tools, new_tools = ToolSchemaFakeTools(), ToolSchemaFakeTools()
        await send_bootstrap(adapter, tools=old_tools)
        old_token = requested_approval_token(old_tools)
        if restart:
            await adapter.cleanup_all()
            await adapter.on_started("Agent", "A coding agent")
        else:
            await adapter.on_cleanup(ROOM_ID)
        await send_bootstrap(adapter, tools=new_tools)
        new_token = requested_approval_token(new_tools)
        late_answer = ToolSchemaFakeTools()
        await adapter.try_handle_control(
            make_platform_message(content=f"/{CodexCommand.APPROVE} {old_token}"),
            late_answer,
        )

        assert late_answer.messages_sent[-1]["content"] == (
            f"Unknown approval id `{old_token}`. Pending: {new_token}"
        )
        assert new.responses == []
        await adapter.try_handle_control(
            make_platform_message(content=f"/{CodexCommand.APPROVE} {new_token}"),
            ToolSchemaFakeTools(),
        )
        await await_released_turn(adapter, ROOM_ID)
        assert new.responses == [(10, ACCEPTED)]

    async def test_a_held_session_approval_notice_cannot_authorize_a_rejoined_room(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
    ) -> None:
        old = FakeCodexClient(
            events=[
                event_request(
                    10,
                    CodexApprovalMethod.COMMAND_EXECUTION,
                    {"approvalId": "old", "command": "npm test"},
                ),
                turn_completed(),
            ]
        )
        other = FakeCodexClient(
            events=[
                event_request(
                    10,
                    CodexApprovalMethod.COMMAND_EXECUTION,
                    {"approvalId": "other", "command": "npm test"},
                ),
                turn_completed(),
            ]
        )
        new = FakeCodexClient(
            events=[
                event_request(
                    10,
                    CodexApprovalMethod.COMMAND_EXECUTION,
                    {"approvalId": "new", "command": "npm test"},
                ),
                turn_completed(),
            ]
        )
        adapter = await started_codex(
            old,
            other,
            new,
            approval_mode="manual",
            approval_wait_timeout_s=PATIENT_APPROVAL_S,
        )
        old_tools = ToolSchemaFakeTools()
        await send_bootstrap(adapter, tools=old_tools)
        old_token = requested_approval_token(old_tools)
        old_residency = residency_of(adapter, ROOM_ID)
        assert old_residency is not None
        other_tools = ToolSchemaFakeTools()
        await send_bootstrap(adapter, room_id="other-room", tools=other_tools)
        other_token = requested_approval_token(other_tools)
        control_tools = ToolSchemaFakeTools()
        acknowledgment = control_tools.hold_message("resolved")
        approving = asyncio.create_task(
            adapter.try_handle_control(
                make_platform_message(
                    content=f"/{CodexCommand.APPROVE_SESSION} {old_token}"
                ),
                control_tools,
            )
        )

        async with acknowledgment:
            await await_released_turn(adapter, ROOM_ID)
            await adapter.on_cleanup(ROOM_ID)
            rejoined_tools = ToolSchemaFakeTools()
            await send_bootstrap(adapter, tools=rejoined_tools)
            rejoined_token = requested_approval_token(rejoined_tools)
            new_residency = residency_of(adapter, ROOM_ID)
            assert new_residency is not None
            assert new_residency is not old_residency
            assert new_residency.generation == old_residency.generation
            await adapter.try_handle_control(
                make_platform_message(content=f"/{CodexCommand.APPROVE} {old_token}"),
                ToolSchemaFakeTools(),
            )

        assert await approving
        assert old.responses == [(10, {"decision": "acceptForSession"})]
        assert new.responses == other.responses == []
        await adapter.try_handle_control(
            make_platform_message(
                room_id="other-room", content=f"/{CodexCommand.APPROVE} {other_token}"
            ),
            ToolSchemaFakeTools(),
        )
        await await_released_turn(adapter, "other-room")
        await adapter.try_handle_control(
            make_platform_message(content=f"/{CodexCommand.APPROVE} {rejoined_token}"),
            ToolSchemaFakeTools(),
        )
        await await_released_turn(adapter, ROOM_ID)

        assert new.responses == other.responses == [(10, ACCEPTED)]
        assert old.closed
        assert not new.closed and not other.closed
