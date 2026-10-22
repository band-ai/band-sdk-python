"""Transport and protocol failures fail waiters and reap their subprocesses."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.integrations.omprpc import OmpProcessError, OmpProtocolError
from tests.integrations.omppeer import FRAME_PAYLOAD_MARK
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    owned_rpc_process,
    prompt_task,
)


class TestTransportAndProtocolFailures:
    @pytest.mark.parametrize(
        ("scenario", "error", "match"),
        [
            ("eof", OmpProcessError, "EOF"),
            ("bad-json", OmpProtocolError, "protocol"),
            ("bad-event", OmpProtocolError, "protocol"),
            ("wrong-command", OmpProtocolError, "command"),
            ("missing-state", OmpProtocolError, "protocol"),
            ("bad-acceptance", OmpProtocolError, "protocol"),
        ],
    )
    async def test_eof_and_protocol_failure_cannot_leave_a_prompt_waiting(
        self,
        client_factory: ClientFactory,
        scenario: str,
        error: type[RuntimeError],
        match: str,
    ) -> None:
        client = await client_factory(scenario)
        process = owned_rpc_process(client)
        async with asyncio.timeout(6):
            with pytest.raises(error, match=match):
                await prompt_task(client, Observer())
        assert process.returncode is not None

    async def test_protocol_failure_logs_omit_the_frame_payload(
        self,
        client_factory: ClientFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = await client_factory("bad-json")
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="band.integrations.omprpc.client"):
            async with asyncio.timeout(6):
                with pytest.raises(OmpProtocolError, match="protocol"):
                    await prompt_task(client, Observer())
        assert FRAME_PAYLOAD_MARK not in caplog.text

    async def test_eof_fails_a_command_waiter_and_closes_the_transport(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        process = owned_rpc_process(client)
        async with asyncio.timeout(6):
            with pytest.raises(OmpProcessError, match="EOF"):
                await client.request("crash")
            await client.close()
        assert process.returncode is not None

    async def test_stderr_is_drained_without_newlines(
        self,
        client_factory: ClientFactory,
    ) -> None:
        client = await client_factory("basic")
        async with asyncio.timeout(5):
            assert await client.request("stderr") == {"drained": True}

    @pytest.mark.parametrize(
        "scenario",
        [
            "chunk-overflow",
            "chunk-order",
            "chunk-interrupted",
            "chunk-invalid-data",
            "chunk-length",
            "chunk-eof",
            "frame-overflow",
        ],
    )
    async def test_invalid_or_oversized_native_chunks_fail_and_reap(
        self, client_factory: ClientFactory, scenario: str
    ) -> None:
        client = await client_factory(scenario)
        process = owned_rpc_process(client)
        async with asyncio.timeout(6):
            with pytest.raises(OmpProtocolError, match="protocol"):
                await prompt_task(client, Observer())
        assert process.returncode is not None
