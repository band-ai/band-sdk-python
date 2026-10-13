"""A provider failure recovers the room's process through its owner."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from acp.helpers import update_agent_message_text

from band.core.protocols import FAILURE_CODE_TIMEOUT
from band.core.retention import Residency
from band.testing import FakeAgentTools, reported_failures
from tests.integrations.acp.acp_toolkit.harness import (
    released,
    send_to,
)
from tests.integrations.acp.clientadapter.kit import adapter_on, scripted_conn


class TestACPClientAdapterDeadConnectionRecovery:
    """A provider failure recovers the room's process through its owner."""

    @pytest.mark.asyncio
    async def test_prompt_error_recovers_the_process_and_keeps_the_session(
        self,
    ) -> None:
        conn = scripted_conn({"room-1": "sess-1"})
        conn.prompt = AsyncMock(side_effect=RuntimeError("Process died"))
        adapter = adapter_on(conn)
        tools = FakeAgentTools()

        with pytest.raises(RuntimeError, match="Process died"):
            await send_to(adapter, "room-1", tools)
        room = adapter._rooms["room-1"]
        await released(room)

        assert room.runtime is None
        assert room.session is not None
        assert (room.session.session_id, room.session.live) == ("sess-1", False)
        assert len(reported_failures(tools)) == 1

    @pytest.mark.asyncio
    async def test_closing_thought_delivery_failure_leaves_connection_up(self) -> None:
        """The agent answered fine; posting its reply to the room is what
        failed. That must not recover a healthy process, nor be reported as
        an ACP provider failure."""
        conn = scripted_conn({"room-1": "sess-1"})
        adapter = adapter_on(conn)

        async def prompt_with_reply(**kwargs: object) -> None:
            client = adapter._rooms["room-1"].runtime.client
            await client.session_update(
                kwargs["session_id"], update_agent_message_text("Here's the answer")
            )

        conn.prompt = AsyncMock(side_effect=prompt_with_reply)
        tools = FakeAgentTools()

        async def _raise(*args: object, **kwargs: object) -> None:
            raise RuntimeError("platform rejected the message")

        tools.send_event = _raise  # type: ignore[method-assign]

        await send_to(adapter, "room-1", tools)

        room = adapter._rooms["room-1"]
        assert room.retained.snapshot.state == Residency.RESIDENT
        assert room.runtime is not None and not room.runtime.closed
        assert not reported_failures(tools)

    @pytest.mark.asyncio
    async def test_session_bookkeeping_failure_leaves_connection_up(self) -> None:
        """A failed session task event must not turn a completed prompt into an ACP failure."""
        adapter = adapter_on(scripted_conn({"room-1": "sess-1"}))
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("platform rejected the task event")

        await send_to(adapter, "room-1", tools)

        room = adapter._rooms["room-1"]
        assert room.retained.snapshot.state == Residency.RESIDENT
        assert not reported_failures(tools)

    @pytest.mark.asyncio
    async def test_turn_timeout_preserves_other_room_connection(self) -> None:
        """A timed-out room must not interrupt another room's prompt."""
        conn = scripted_conn({"room-a": "sess-a", "room-b": "sess-b"})
        adapter = adapter_on(conn, turn_timeout_s=1)
        b_started = asyncio.Event()
        release_b = asyncio.Event()

        async def prompt(*, session_id: str, **kwargs: object) -> None:
            if session_id == "sess-b":
                b_started.set()
                await release_b.wait()
            else:
                await asyncio.sleep(10)

        conn.prompt = AsyncMock(side_effect=prompt)
        b_turn = asyncio.create_task(send_to(adapter, "room-b"))
        await b_started.wait()
        adapter.config = adapter.config.model_copy(update={"turn_timeout_s": 0.01})
        tools_a = FakeAgentTools()

        with pytest.raises(TimeoutError):
            await send_to(adapter, "room-a", tools_a)
        room_a, room_b = adapter._rooms["room-a"], adapter._rooms["room-b"]
        await released(room_a)

        assert not b_turn.done()
        assert room_a.runtime is None
        assert room_b.live_session is not None
        assert room_b.live_session[0] == "sess-b"
        conn.cancel.assert_awaited_once_with("sess-a")
        failures = reported_failures(tools_a)
        assert len(failures) == 1
        assert failures[0]["provider"] == "acp"
        assert failures[0]["code"] == FAILURE_CODE_TIMEOUT

        release_b.set()
        await b_turn
