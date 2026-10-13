"""Codex adapter: a closing room shares one close and admits work again only once it succeeds."""

from __future__ import annotations

import asyncio
import re

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.core.retention import RoomRetentionConfig
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    CLOSE_BOUND_S,
    ROOM_ID,
    CleaningTurn,
    CountingCloseClient,
    disposed,
    send_bootstrap,
)
from tests.adapters.codexturns import (
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

    @pytest.mark.looptime
    async def test_a_leave_reported_unfinished_readmits_the_room_once_retried(
        self,
    ) -> None:
        client = CountingCloseClient(
            events=[turn_completed(), turn_completed("turn-2")], hold=True
        )
        adapter = make_codex_adapter(
            client,
            config=CodexAdapterConfig(
                client_close_timeout_s=None,
                retention=RoomRetentionConfig(transition_timeout_s=1),
            ),
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter)
        residency = adapter._room_clients[ROOM_ID].retained
        with pytest.raises(CleanupIncompleteError):
            await adapter.on_cleanup(ROOM_ID)
        client.finish_close.set()
        await disposed(residency)

        await adapter.on_cleanup(ROOM_ID)
        await send_bootstrap(adapter)

        assert len(client.params_of(CodexRequestMethod.TURN_START)) == 2

    @pytest.mark.looptime
    async def test_a_close_past_its_bound_names_the_room_and_bound(self) -> None:
        client = CountingCloseClient(events=[turn_completed()], hold=True)
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(client_close_timeout_s=CLOSE_BOUND_S)
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter)

        with pytest.raises(
            TimeoutError,
            match=re.escape(f"room {ROOM_ID} did not close within {CLOSE_BOUND_S}s"),
        ):
            await adapter.on_cleanup(ROOM_ID)
        client.finish_close.set()
        await adapter.on_cleanup(ROOM_ID)

    async def test_stop_keeps_the_room_fenced_until_worker_cleanup_finishes(
        self, cleaning_turn: CleaningTurn
    ) -> None:
        room, client = cleaning_turn.room, cleaning_turn.client
        await cleaning_turn.stop_twice()

        with pytest.raises(CleanupIncompleteError):
            await room.adapter.on_cleanup(ROOM_ID)
        assert not client.closed
        with pytest.raises(InputNotAccepted):
            await room.send("overlap")

    async def test_concurrent_stops_share_one_worker_cleanup(
        self, cleaning_turn: CleaningTurn
    ) -> None:
        stops = asyncio.create_task(cleaning_turn.stop_twice())
        cleaning_turn.client.release.set()
        await stops

        await cleaning_turn.room.adapter.on_cleanup(ROOM_ID)
        assert cleaning_turn.client.cleanups == 1
        assert cleaning_turn.client.closed
