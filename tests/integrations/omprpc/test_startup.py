"""Startup requires native protocol negotiation and reaps failed launches."""

from __future__ import annotations

import asyncio
import logging
import sys

import pytest

from band.integrations.omprpc import (
    OmpProcessError,
    OmpProtocolError,
    OmpRpcClient,
    OmpRpcError,
)
from tests.integrations.omppeer import OVERSIZE_AFTER_READY
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    ClientFactory,
    SpawnBarrier,
    owned_rpc_process,
)


class TestStartupNegotiation:
    async def test_cancelled_startup_reaps_a_process_that_never_announces_ready(
        self,
    ) -> None:
        barrier = SpawnBarrier(asyncio.create_subprocess_exec)
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                OMP_PEER_SCRIPT,
                "--scenario",
                "no-ready",
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
            ("no-v2", OmpProtocolError),
            ("bad-negotiation", OmpProtocolError),
            ("negotiation-rejected", OmpRpcError),
        ],
    )
    async def test_start_requires_successful_native_v2_negotiation(
        self, client_factory: ClientFactory, scenario: str, error: type[RuntimeError]
    ) -> None:
        async with asyncio.timeout(6):
            with pytest.raises(error, match="protocol"):
                await client_factory(scenario)

    async def test_negotiated_frame_limit_applies_before_the_next_line(self) -> None:
        client = OmpRpcClient(
            (sys.executable, "-u", OMP_PEER_SCRIPT, "--scenario", OVERSIZE_AFTER_READY)
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
        client = await client_factory("basic")
        with caplog.at_level(logging.ERROR, logger="band.integrations.omprpc"):
            await client.close()
        assert caplog.records == []
