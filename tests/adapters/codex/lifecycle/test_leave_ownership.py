"""A Codex room's leave owns its process until the close is confirmed."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.codex import CodexAdapter
from band.core.exceptions import InputNotAccepted
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import (
    CLEANUP_FAILURE,
    PeerCommand,
    memo_instruction,
    write_instruction,
)


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
        host: RetentionHost,
    ) -> None:
        assert isinstance(host.adapter, CodexAdapter)
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.adapter.config = host.adapter.config.model_copy(
            update={"client_close_timeout_s": 0.05}
        )
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
        host.adapter.config = host.adapter.config.model_copy(
            update={"client_close_timeout_s": None}
        )
        barrier.released.set()
        await host.leave("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    async def test_leaving_during_startup_closes_the_half_started_process(
        self,
        host: RetentionHost,
    ) -> None:
        assert isinstance(host.adapter, CodexAdapter)
        host.adapter.config = host.adapter.config.model_copy(
            update={"turn_settle_timeout_s": 0.01}
        )
        starting = host.next_start = Hold()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await starting.reached.wait()

        await host.leave("room-a")

        with pytest.raises(asyncio.CancelledError):
            await opening
        assert host.exited == [True]
        assert host.native_id("room-a") is None
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
