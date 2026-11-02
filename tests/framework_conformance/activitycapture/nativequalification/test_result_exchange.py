"""Native commands round-trip opaque results through independently held I/O."""

from __future__ import annotations

import os
from http import HTTPStatus

import httpx
import pytest

from tests.e2e.baseline.toolkit.resultexchange import ExchangeOperation, ResultExchange
from tests.framework_conformance.activitycapture.nativequalification.kit import (
    NativeCommands,
)
from tests.hostshells import HOST_SHELLS, host_shell_id

COMMAND_LINE_ENDING = os.linesep.encode("ascii")


class TestResultExchange:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_producer_commit_and_held_consumer_share_only_generated_bytes(
        self, exchange: ResultExchange, native_commands: NativeCommands
    ) -> None:
        producer = await native_commands.start(ExchangeOperation.PRODUCE)
        await exchange.wait(exchange.producer.entered, phase="producer entered")
        consumer = await native_commands.start(ExchangeOperation.CONSUME)
        await exchange.wait(exchange.consumer.entered, phase="consumer entered")

        exchange.producer.release.set()
        produced, producer_error = await producer.communicate()
        assert (producer.returncode, produced, producer_error) == (
            0,
            exchange.result + COMMAND_LINE_ENDING,
            b"",
        )
        await exchange.wait(exchange.committed, phase="producer committed")

        exchange.consumer.release.set()
        consumed, consumer_error = await consumer.communicate()
        assert (consumer.returncode, consumed, consumer_error) == (
            0,
            exchange.result + COMMAND_LINE_ENDING,
            b"",
        )

    @pytest.mark.parametrize("shell", HOST_SHELLS, ids=host_shell_id)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_agent_command_line_runs_unchanged_in_each_host_shell(
        self,
        exchange: ResultExchange,
        native_commands: NativeCommands,
        shell: str | None,
    ) -> None:
        # The privacy sentinel only checks something if the command line carries it.
        assert exchange.note in exchange.command(ExchangeOperation.PRODUCE)
        producer = await native_commands.run_in_shell(ExchangeOperation.PRODUCE, shell)
        await exchange.wait(exchange.producer.entered, phase="producer entered")
        exchange.producer.release.set()
        produced, error = await producer.communicate()
        assert (producer.returncode, produced, error) == (
            0,
            exchange.result + COMMAND_LINE_ENDING,
            b"",
        )
        await exchange.wait(exchange.committed, phase="producer committed")

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unproduced_commit_and_premature_consume_fail(
        self, exchange: ResultExchange
    ) -> None:
        async with httpx.AsyncClient(trust_env=False) as client:
            commit = await client.post(
                exchange.url(ExchangeOperation.COMMIT), content=b"unknown"
            )
            assert commit.status_code == HTTPStatus.CONFLICT
            exchange.consumer.release.set()
            consume = await client.get(exchange.url(ExchangeOperation.CONSUME))
            assert consume.status_code == HTTPStatus.CONFLICT

    @pytest.mark.asyncio(loop_scope="session")
    async def test_failed_scenario_releases_actual_pending_http_work(
        self, exchange: ResultExchange, native_commands: NativeCommands
    ) -> None:
        producer = await native_commands.start(ExchangeOperation.PRODUCE)
        async with exchange.releasing_on_exit():
            await exchange.wait(exchange.producer.entered, phase="producer entered")
        output, error = await producer.communicate()
        assert (producer.returncode, output, error) == (
            0,
            exchange.result + COMMAND_LINE_ENDING,
            b"",
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_commit_cannot_be_launched_as_a_separate_command(
        self, exchange: ResultExchange
    ) -> None:
        with pytest.raises(ValueError, match="Commit belongs to the producer command"):
            exchange.argv(ExchangeOperation.COMMIT)
