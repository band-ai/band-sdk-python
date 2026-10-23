"""Transport and protocol failures fail waiters and reap their subprocesses."""

from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import JsonValue

from band.integrations.codex.winspawn import await_returncode
from band.integrations.omprpc import OmpProcessError, OmpProtocolError
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeer import FRAME_PAYLOAD_MARK
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    ProcessGate,
    descendant_client,
    descendant_liveness,
    owned_rpc_process,
    prompt_task,
    unrelated_gate_child,
    wait_for_descendants,
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

    async def test_observed_root_exit_retires_an_inflight_prompt_and_owned_children(
        self, process_gate: ProcessGate
    ) -> None:
        client = descendant_client(process_gate, stdio="inherit", detached=True)
        observer = Observer()
        running: asyncio.Task[None] | None = None
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = await wait_for_descendants(process_gate)
                process = owned_rpc_process(client)
                running = prompt_task(client, observer)
                await observer.wait_for(OmpEventType.AGENT_END)
                crashing = asyncio.create_task(client.request("crash"))
                assert await await_returncode(process, float("inf"))
                assert await descendant_liveness(descendants) == [True, True]

                with pytest.raises(OmpProcessError):
                    await client.request("get_state")
                async with asyncio.timeout(5):
                    with pytest.raises(OmpProcessError):
                        await running
                    with pytest.raises(OmpProcessError):
                        await crashing
                assert await descendant_liveness(descendants) == [False, False]
                assert await descendant_liveness([unrelated]) == [True]
            finally:
                await client.close()
                tasks = [task for task in (running, crashing) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_stderr_is_drained_without_newlines(
        self,
        client_factory: ClientFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = await client_factory("basic")
        with caplog.at_level(logging.DEBUG, logger="band.integrations.omprpc.client"):
            async with asyncio.timeout(5):
                assert await client.request("stderr") == {"drained": True}
        leaked = any(
            FRAME_PAYLOAD_MARK in record.getMessage() for record in caplog.records
        )
        caplog.clear()
        assert not leaked

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
