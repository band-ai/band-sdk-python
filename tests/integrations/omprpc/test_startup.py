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
from tests.integrations.omppeer import READY_LIMIT_BURST
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    ClientFactory,
    owned_rpc_process,
)


class TestStartupNegotiation:
    async def test_ready_limits_apply_before_the_next_buffered_frame(self) -> None:
        client = OmpRpcClient(
            (sys.executable, "-u", OMP_PEER_SCRIPT, "--scenario", READY_LIMIT_BURST)
        )
        try:
            with pytest.raises(OmpProtocolError):
                await client.start()
            assert owned_rpc_process(client).returncode is not None
        finally:
            await client.close()

    async def test_cancelled_startup_reaps_a_process_that_never_announces_ready(
        self,
    ) -> None:
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                OMP_PEER_SCRIPT,
                "--scenario",
                "no-ready",
            )
        )
        starting = asyncio.create_task(client.start())
        try:
            async with asyncio.timeout(5):
                while client._proc is None and not starting.done():
                    await asyncio.sleep(0)
            assert client._proc is not None
            assert starting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert client._proc.returncode is not None
        finally:
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

    async def test_graceful_close_does_not_log_stdout_eof_as_a_crash(
        self, client_factory: ClientFactory, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = await client_factory("basic")
        with caplog.at_level(logging.ERROR, logger="band.integrations.omprpc"):
            await client.close()
        assert caplog.records == []
