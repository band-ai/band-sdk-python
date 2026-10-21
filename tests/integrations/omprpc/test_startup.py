"""Startup requires native protocol negotiation and reaps failed launches."""

from __future__ import annotations

import asyncio
import sys

import pytest

from band.integrations.omprpc import (
    OmpProcessError,
    OmpProtocolError,
    OmpRpcClient,
    OmpRpcError,
)
from tests.integrations.omprpc.kit import OMP_PEER_SCRIPT, ClientFactory


class TestStartupNegotiation:
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
        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await client.start()
            assert client._proc is not None
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
