"""An adapter owner's failure never strands another adapter's resources."""

from __future__ import annotations

import asyncio

import pytest

from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.helpers import idle_client, run_single_turn
from tests.adapters.opencode.ownership import AdapterOwner
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestFixtureCleanup:
    @pytest.mark.parametrize("failure", ["body", "backend-close"])
    async def test_every_adapter_is_closed_after_a_failure(self, failure: str) -> None:
        async with controlled_clock():
            before = asyncio.all_tasks()
            clients = [idle_client(), idle_client()]
            healthy, faulty = FakeBandMCPBackend(), FakeBandMCPBackend()
            if failure == "backend-close":
                faulty.fail_stops = 1
            with (
                backends_created_by(healthy, faulty),
                pytest.raises(
                    (AssertionError, OSError), match="test failed|stop failed"
                ),
            ):
                async with AdapterOwner() as adapters:
                    for client in clients:
                        await run_single_turn(
                            adapters.create(client),
                            FakeAgentTools(participants=ROOM_MEMBERS),
                        )
                    if failure == "body":
                        raise AssertionError("test failed")

            assert all(client.closed for client in clients)
            assert all(client.serve_registrations == {} for client in clients)
            assert asyncio.all_tasks() == before

            async with AdapterOwner() as adapters:
                independent = idle_client()
                await run_single_turn(
                    adapters.create(independent),
                    FakeAgentTools(participants=ROOM_MEMBERS),
                )
            assert independent.closed
            assert asyncio.all_tasks() == before

    async def test_a_failed_explicit_close_keeps_its_reader_owned(self) -> None:
        async with controlled_clock():
            before = asyncio.all_tasks()
            client = idle_client()
            backend = FakeBandMCPBackend()
            with backends_created_by(backend):
                async with AdapterOwner() as adapters:
                    adapter = adapters.create(client)
                    await run_single_turn(
                        adapter, FakeAgentTools(participants=ROOM_MEMBERS)
                    )
                    backend.fail_stops = 1
                    with pytest.raises(OSError, match="stop failed"):
                        await adapter.on_cleanup("room-1")

            assert client.closed
            assert client.serve_registrations == {}
            assert asyncio.all_tasks() == before
