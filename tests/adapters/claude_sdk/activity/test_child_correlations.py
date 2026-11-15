"""Completed native agents release child identities without retiring nested work."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.claude_sdk.activity import MAX_PENDING_IDENTITIES, Origin
from band.runtime.activity import tool_call_activity
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.activity.kit import (
    BACKGROUND_CALL,
    BACKGROUND_TASK,
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import ClaudeRoom, OpenRoom
from tests.adapters.hold import Hold
from tests.baseline.decisions import ToolCall


class TestClaudeChildCorrelations:
    async def test_completed_async_agents_do_not_exhaust_later_foreground_hooks(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        for revision in range(2):
            room.claude.script(
                [
                    room.model_reply(f"Started {revision}."),
                    native_tool_uses((BACKGROUND_CALL, "Agent")),
                    native_task_started(BACKGROUND_TASK, BACKGROUND_CALL),
                    native_tool_result(BACKGROUND_CALL),
                ]
            )
            await room.send("Start asynchronous agent")
            session = room.session_manager.session(room.room_id)
            assert session is not None and session.activity is not None
            origin = session.activity.resolve(BACKGROUND_CALL)
            peer = room.claude.sessions[-1]
            # Explicit async Agent runs emit hooks without mirrored child results.
            for index in range(MAX_PENDING_IDENTITIES - 1):
                assert (
                    await peer._pre_tool_use(
                        f"child-{revision}-{index}",
                        ToolCall("Read", {"file_path": "notes.md"}),
                        agent_id=BACKGROUND_TASK,
                    )
                    is None
                )
            peer.native_event(
                type="system",
                subtype="task_updated",
                task_id=BACKGROUND_TASK,
                patch={"status": "completed"},
            )
            await origin.owner.task
            with pytest.raises(ValueError, match="Unknown Claude tool origin"):
                session.activity.resolve(f"child-{revision}-0")
            hold = Hold()
            room.claude.script(
                [hold, room.model_reply(f"Foreground complete {revision}.")]
            )
            async with asyncio.TaskGroup() as group:
                foreground = group.create_task(room.send("Continue foreground"))
                try:
                    await hold.arrived()
                    for index in range(2):
                        assert (
                            await peer._pre_tool_use(
                                f"foreground-{revision}-{index}",
                                ToolCall("Read", {"file_path": "notes.md"}),
                            )
                            is None
                        )
                finally:
                    hold.released.set()
                await foreground
        assert room.chat == [
            "Started 0.",
            "Foreground complete 0.",
            "Started 1.",
            "Foreground complete 1.",
        ]

    async def test_parent_completion_preserves_an_independent_nested_agent(
        self,
        background_origin: tuple[ClaudeRoom, FakeAgentTools, ActivityRecorder, Origin],
    ) -> None:
        room, original, _, parent = background_origin
        session = room.session_manager.session(room.room_id)
        assert session is not None and session.activity is not None
        peer = room.claude.sessions[-1]
        nested_call, nested_task = "nested-call", "nested-task"
        assert (
            await peer._pre_tool_use(
                nested_call, ToolCall("Agent", {}), agent_id=BACKGROUND_TASK
            )
            is None
        )
        peer.native_event(
            type="system",
            subtype="task_started",
            task_id=nested_task,
            tool_use_id=nested_call,
            description="Nested work",
            uuid="nested",
            session_id="session-1",
        )
        assert (
            await peer._pre_tool_use(
                "nested-child",
                ToolCall("Read", {"file_path": "notes.md"}),
                agent_id=nested_task,
            )
            is None
        )
        nested = session.activity.resolve(nested_call)
        peer.native_event(
            type="system",
            subtype="task_updated",
            task_id=BACKGROUND_TASK,
            patch={"status": "completed"},
        )
        await parent.owner.task
        nested.require_authority()
        assert not nested.owner.task.done()
        assert session.activity.resolve("nested-child") is nested
        await peer.background_tool(
            nested_task,
            "nested-reply",
            ToolCall(
                "mcp__band__band_send_message",
                {"content": "Nested result.", "mentions": ["@bob"]},
            ),
        )
        assert original.messages_sent[-1]["content"] == "Nested result."
        peer.native_event(
            type="system",
            subtype="task_updated",
            task_id=nested_task,
            patch={"status": "completed"},
        )
        await nested.owner.task
        with pytest.raises(ValueError, match="Unknown Claude tool origin"):
            session.activity.resolve("nested-child")

    @pytest.mark.parametrize(
        ("terminal_task", "terminal_run"),
        [(BACKGROUND_TASK, "stale-run"), ("unknown-task", "current-run")],
        ids=["stale-run", "unknown-task"],
    )
    async def test_unmatched_terminal_keeps_the_actual_resource_correlations(
        self, claude_room: OpenRoom, terminal_task: str, terminal_run: str
    ) -> None:
        room = await claude_room(emit=())
        recorder = ActivityRecorder()
        recorder.observing(room.tools)
        room.claude.script(
            [
                room.model_reply("Started."),
                native_tool_uses((BACKGROUND_CALL, "Agent")),
                Raw(
                    {
                        **native_task_started(BACKGROUND_TASK, BACKGROUND_CALL).message,
                        "run_id": "current-run",
                    }
                ),
                native_tool_result(BACKGROUND_CALL),
            ]
        )
        await room.send("Start asynchronous agent")
        session = room.session_manager.session(room.room_id)
        assert session is not None and session.activity is not None
        origin = session.activity.resolve(BACKGROUND_CALL)
        peer = room.claude.sessions[-1]
        assert (
            await peer._pre_tool_use(
                "child-check",
                ToolCall("Read", {"file_path": "notes.md"}),
                agent_id=BACKGROUND_TASK,
            )
            is None
        )
        peer.native_event(
            type="system",
            subtype="task_updated",
            task_id=terminal_task,
            run_id=terminal_run,
            patch={"status": "completed"},
        )
        peer.native_event(
            **{
                **native_tool_uses(("child-check", "Read")).message,
                "parent_tool_use_id": BACKGROUND_CALL,
            }
        )
        await recorder.wait_for_detail(tool_call_activity("Read"))
        assert session.activity.resolve("child-check") is origin
        origin.require_authority()
        peer.native_event(
            type="system",
            subtype="task_updated",
            task_id=BACKGROUND_TASK,
            run_id="current-run",
            patch={"status": "completed"},
        )
        await origin.owner.task
        with pytest.raises(ValueError, match="Unknown Claude tool origin"):
            session.activity.resolve("child-check")
