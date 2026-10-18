"""An adapter owner's failure never strands another adapter's resources."""

from __future__ import annotations

import asyncio

import pytest

from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_session_idle,
    run_single_turn,
)
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
            clients = [
                FakeOpencodeClient(
                    prompt_event_sequences=[[event_session_idle("sess-1")]]
                )
                for _ in range(2)
            ]
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
                        await run_single_turn(adapters.create(client), FakeAgentTools())
                    if failure == "body":
                        raise AssertionError("test failed")

            assert all(client.closed for client in clients)
            assert all(client.serve_registrations == {} for client in clients)
            assert asyncio.all_tasks() == before

            async with AdapterOwner() as adapters:
                independent = FakeOpencodeClient(
                    prompt_event_sequences=[[event_session_idle("sess-1")]]
                )
                await run_single_turn(adapters.create(independent), FakeAgentTools())
            assert independent.closed
            assert asyncio.all_tasks() == before
