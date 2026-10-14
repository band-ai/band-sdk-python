"""Characterize resource ownership until the adapter defines a teardown contract."""

from __future__ import annotations

import pytest

from band.adapters.nooa import NooaAdapter
from band.testing import FakeAgentTools
from tests.adapters.nooa.kit import ROOM_ID, run_turn
from tests.adapters.nooa.resources import WorkerOwner


class TestResourceLifecycle:
    @pytest.mark.parametrize(
        "boundary", ["room-removal", "session-replacement", "shutdown"]
    )
    async def test_owned_worker_still_requires_explicit_owner_teardown(
        self, boundary: str
    ) -> None:
        owner = WorkerOwner()
        adapter = NooaAdapter(agent=owner.agent_class(), emit=())
        await adapter.on_started("Agent", "A resource-owning agent")

        try:
            await run_turn(adapter, FakeAgentTools())
            worker = owner.workers[0]

            match boundary:
                case "room-removal":
                    await adapter.on_cleanup(ROOM_ID)
                    await adapter.on_cleanup(ROOM_ID)
                case "session-replacement":
                    await run_turn(adapter, FakeAgentTools(), is_session_bootstrap=True)
                    assert len(owner.workers) == 2
                case "shutdown":
                    await adapter.cleanup_all()
                    await adapter.cleanup_all()

            assert owner.agent_close_calls == 0
            assert worker.running
            assert not worker.closed.is_set()
        finally:
            await owner.close()

        assert worker.closed.is_set()
        assert not worker.running
