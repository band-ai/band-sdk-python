"""Post-turn command completion belongs to its original native resource."""

from __future__ import annotations

import asyncio

from band.runtime.activity import tool_call_activity
from band.testing.activity import ActivityRecorder
from tests.adapters.codex.activity.kit import (
    DOCS_LOOKUP,
    LIST_FILES,
    item_completed,
    item_started,
)
from tests.adapters.codex.activity.nativecommands import CommandClient
from tests.adapters.codex.kit import ROOM_ID, ToolSchemaFakeTools, agent_input
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from band.runtime.tools.types import BandTool


class TestCodexBackgroundCommands:
    async def test_stop_ack_waits_for_old_item_completion_and_preserves_new_turn(
        self,
    ) -> None:
        recorder = ActivityRecorder()
        client = CommandClient()
        command = {**LIST_FILES, "processId": "42"}
        client.script(
            item_started(command),
            tool_call_request(1, BandTool.NO_REPLY),
            turn_completed(),
        )
        adapter = make_codex_adapter(client, emit=())
        await adapter.on_started("Agent", "Agent")
        new_turn: asyncio.Task[None] | None = None
        try:
            original = recorder.observing(ToolSchemaFakeTools())
            await adapter.on_event(agent_input("Start work", original))
            room = adapter._room_clients[ROOM_ID]
            (resource,) = room.commands.values()
            assert resource.owner is not None
            assert not resource.owner.task.done()
            assert recorder.activity.detail == tool_call_activity("exec")
            assert await recorder.next_start() == tool_call_activity("exec")

            client.script(item_started(DOCS_LOOKUP))
            current = recorder.observing(ToolSchemaFakeTools())
            async with asyncio.TaskGroup() as group:
                new_turn = group.create_task(
                    adapter.on_event(
                        agent_input("New work", current, is_session_bootstrap=False)
                    )
                )
                assert await recorder.next_start() == tool_call_activity(
                    "mcp:docs/search"
                )
                assert recorder.activity.detail == tool_call_activity("mcp:docs/search")
                resource.owner.terminate()
                assert await client.stops.get() == {
                    "threadId": FIRST_THREAD_ID,
                    "processId": "42",
                }
                assert not resource.owner.task.done()
                assert not new_turn.done()

                client.emit_turn("turn-1", item_completed(command))
                await resource.owner.task
                assert recorder.activity.detail == tool_call_activity("mcp:docs/search")
                assert not new_turn.done()
                client.emit_turn(
                    "turn-2",
                    item_completed(DOCS_LOOKUP),
                    tool_call_request(
                        2,
                        BandTool.SEND_MESSAGE,
                        {"content": "New result.", "mentions": ["alice"]},
                    ),
                    turn_completed(),
                )
                await new_turn
                assert [message["content"] for message in current.messages_sent] == [
                    "New result."
                ]
        finally:
            if new_turn is not None and not new_turn.done():
                new_turn.cancel()
                await asyncio.gather(new_turn, return_exceptions=True)
            await adapter.cleanup_all()
