"""Codex adapter: a closing room shares one close and admits work again only once it succeeds."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.client.streaming import ControlMode
from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    ROOM_ID,
    CodexRoom,
    CountingCloseClient,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
)
from tests.conftest import eager_tasks, requires_eager_tasks


class TestRoomRetirement:
    """A closing room shares one close and admits work again only once it
    succeeds."""

    @requires_eager_tasks
    async def test_cleanup_readmits_and_recloses_the_room_under_an_eager_task_factory(
        self,
    ) -> None:
        client = CountingCloseClient(
            events=[turn_completed(), turn_completed("turn-2")]
        )
        adapter = make_codex_adapter(client)
        with eager_tasks():
            await adapter.on_started("Codex Agent", "A coding agent")
            await send_bootstrap(adapter)
            await adapter.on_cleanup(ROOM_ID)
            await send_bootstrap(adapter)
            await adapter.on_cleanup(ROOM_ID)

        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2
        assert client.closes == 2

    @pytest.mark.looptime
    async def test_concurrent_room_cleanups_share_one_close(self) -> None:
        client = CountingCloseClient(events=[turn_completed()], hold=True)
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter)
        room_cleanup = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
        await client.closing.wait()
        every_cleanup = asyncio.create_task(adapter.cleanup_all())
        # Fake time advances only once both cleanups are waiting on the close.
        await asyncio.sleep(1)
        client.finish_close.set()

        await asyncio.gather(room_cleanup, every_cleanup)
        assert client.closes == 1

    async def test_cleanup_cancelled_during_close_still_readmits_the_room(
        self,
    ) -> None:
        client = CountingCloseClient(
            events=[turn_completed(), turn_completed("turn-2")], hold=True
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter)
        cleanup = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
        await client.closing.wait()
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        client.finish_close.set()
        await client.close_finished.wait()

        await adapter.on_cleanup(ROOM_ID)
        await send_bootstrap(adapter)
        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2

    @pytest.mark.parametrize("retain", [False, True])
    async def test_stop_callbacks_share_worker_cleanup(self, retain: bool) -> None:
        started, cleaning, release = (asyncio.Event() for _ in range(3))
        cleanup: list[str] = []

        class CleaningClient(FakeCodexClient):
            async def request(
                self,
                method: str,
                params: dict[str, Any] | None = None,
                *,
                retry_on_overload: bool = True,
            ) -> dict[str, Any]:
                if method != CodexRequestMethod.TURN_START:
                    return await super().request(
                        method, params, retry_on_overload=retry_on_overload
                    )
                started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleanup.append("completed")
                raise AssertionError("The cancelled turn cannot start")

        client = CleaningClient()
        adapter = make_codex_adapter(client)
        room = CodexRoom(adapter, client)
        sending = asyncio.create_task(room.send("work"))
        await started.wait()
        sending.cancel()
        await cleaning.wait()
        callbacks = [
            asyncio.create_task(adapter.on_interrupt(ROOM_ID, ControlMode.STOP))
            for _ in range(2)
        ]
        try:
            if retain:
                await asyncio.gather(*callbacks)
                with pytest.raises(CleanupIncompleteError):
                    await adapter.on_cleanup(ROOM_ID)
                assert not client.closed
                with pytest.raises(InputNotAccepted):
                    await room.send("overlap")
            release.set()
            await asyncio.gather(*callbacks)
            with pytest.raises(asyncio.CancelledError):
                await sending
            await adapter.on_cleanup(ROOM_ID)
            assert cleanup == ["completed"]
            assert client.closed
        finally:
            release.set()
            await asyncio.gather(sending, *callbacks, return_exceptions=True)
            await adapter.cleanup_all()
