"""Startup requires native protocol negotiation and reaps failed launches."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.integrations.omprpc import (
    OmpProcessError,
    OmpProtocolError,
    OmpRpcClient,
    OmpRpcError,
)
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.spawn import (
    OMP_PEER_COMMAND,
    ClientFactory,
    SpawnBarrier,
)
from tests.integrations.ompscenarios import PeerScenario


class TestStartupNegotiation:
    async def test_cancelled_startup_reaps_a_process_that_never_announces_ready(
        self,
    ) -> None:
        barrier = SpawnBarrier(asyncio.create_subprocess_exec)
        client = OmpRpcClient(
            (
                *OMP_PEER_COMMAND,
                "--scenario",
                PeerScenario.NO_READY,
            ),
            create_process=barrier.create,
        )
        starting = asyncio.create_task(client.start())
        try:
            async with asyncio.timeout(5):
                await barrier.created.wait()
            assert barrier.process is not None
            assert starting.cancel()
            barrier.release.set()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert barrier.process.returncode is not None
        finally:
            barrier.release.set()
            await client.close()

    async def test_missing_executable_is_a_process_failure(self) -> None:
        client = OmpRpcClient(("definitely-not-an-omp-executable-xyz",))
        try:
            with pytest.raises(OmpProcessError, match="start"):
                await client.start()
        finally:
            await client.close()

    @pytest.mark.parametrize(
        ("scenario", "error"),
        [
            (PeerScenario.NO_V2, OmpProtocolError),
            (PeerScenario.BAD_NEGOTIATION, OmpProtocolError),
            (PeerScenario.NEGOTIATION_REJECTED, OmpRpcError),
        ],
    )
    async def test_start_requires_successful_native_v2_negotiation(
        self,
        client_factory: ClientFactory,
        scenario: PeerScenario,
        error: type[RuntimeError],
    ) -> None:
        async with asyncio.timeout(6):
            with pytest.raises(error, match="protocol"):
                await client_factory(scenario)

    async def test_negotiated_frame_limit_applies_before_the_next_line(self) -> None:
        client = OmpRpcClient(
            (*OMP_PEER_COMMAND, "--scenario", PeerScenario.OVERSIZE_AFTER_READY)
        )
        try:
            with pytest.raises(OmpProtocolError):
                await client.start()
            assert owned_rpc_process(client).returncode is not None
        finally:
            await client.close()

    async def test_graceful_close_does_not_log_stdout_eof_as_a_crash(
        self, client_factory: ClientFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = await client_factory(PeerScenario.STANDARD_TURN)
        with caplog.at_level(logging.ERROR, logger="band.integrations.omprpc"):
            await client.close()
        assert caplog.records == []
