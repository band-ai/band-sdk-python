"""A Codex room's leave owns its process until the close is confirmed."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted
from band.testing import failure_reports
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import (
    CLEANUP_FAILURE,
    PeerCommand,
    memo_instruction,
    write_instruction,
)
from tests.framework_conformance.turnprobes import DispatchingFakeTools


class TestLifecycleLeave:
    """A Codex room's leave owns its process until the close is confirmed."""

    async def test_a_failed_leave_is_final_until_retried_then_rejoin_starts_fresh(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert await host.send("room-a", memo_instruction("kept")) == ["Noted."]
        left_thread = host.native_id("room-a")
        host.children[0].refuse_close = True
        with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
            await host.leave("room-a")
        host.children[0].refuse_close = False
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", PeerCommand.RECALL)
        assert host.native_id("room-a") == left_thread
        await host.leave("room-a")
        assert host.native_id("room-a") is None
        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: None"]
        assert host.native_id("room-a") not in (None, left_thread)
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        assert host.exited == [True, False]
        assert host.overlaps == 0

    async def test_close_timeout_retains_ownership_until_retry(
        self,
        close_bound_host: RetentionHost,
    ) -> None:
        host = close_bound_host
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        barrier = host.children[0].closing = Hold()
        with pytest.raises(TimeoutError):
            await host.leave("room-a")
        await barrier.reached.wait()
        assert host.exited == [False]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", write_instruction("changed"))
        assert host.exited == [False]
        barrier.released.set()
        await host.leave("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    async def test_leaving_during_startup_closes_the_half_started_process(
        self,
        brief_grace_host: RetentionHost,
    ) -> None:
        host = brief_grace_host
        starting = host.next_start = Hold()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await starting.reached.wait()

        await host.leave("room-a")

        with pytest.raises(asyncio.CancelledError):
            await opening
        assert host.exited == [True]
        assert host.native_id("room-a") is None
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    async def test_a_turn_starting_its_process_at_leave_is_refused_not_failed(
        self,
        host: RetentionHost,
    ) -> None:
        tools = DispatchingFakeTools(room_id="room-a")
        host.hold_startup.touch()
        sending = asyncio.create_task(
            host.deliver("room-a", write_instruction("alpha"), tools)
        )
        await host.start_reached("room-a")
        leaving = asyncio.create_task(host.leave("room-a"))
        await asyncio.sleep(0)
        residency = host.bridge.residency("room-a")
        assert residency is not None and residency.closing

        host.hold_startup.unlink()  # the start finishes inside the settle grace
        await leaving

        with pytest.raises(InputNotAccepted):
            await sending
        assert failure_reports(tools) == []

    async def test_a_message_after_an_abandoned_leave_finished_keeps_no_claim(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        await closing.reached.wait()
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
        closing.released.set()
        await host.forgotten("room-a")

        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: None"]
        await host.leave("room-a")
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
