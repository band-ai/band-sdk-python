"""Owned real command processes for the native exchange boundary tests."""

from __future__ import annotations

import asyncio

from tests.e2e.baseline.toolkit.resultexchange import ExchangeOperation, ResultExchange
from tests.hostshells import shell_argv


class NativeCommands:
    def __init__(self, exchange: ResultExchange) -> None:
        self.exchange = exchange
        self.processes: list[asyncio.subprocess.Process] = []

    async def start(self, operation: ExchangeOperation) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            *self.exchange.argv(operation),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes.append(process)
        return process

    async def run_in_shell(
        self, operation: ExchangeOperation, shell: str | None
    ) -> asyncio.subprocess.Process:
        """Start the command line an agent is given, as ``shell`` runs it."""
        command = self.exchange.command(operation)
        process = await (
            asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            if shell is None
            else asyncio.create_subprocess_exec(
                *shell_argv(shell, command),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        self.processes.append(process)
        return process

    async def close(self) -> None:
        self.exchange.producer.release.set()
        self.exchange.consumer.release.set()
        try:
            async with asyncio.timeout(self.exchange.deadline_s):
                await asyncio.gather(
                    *(process.communicate() for process in self.processes)
                )
        finally:
            for process in self.processes:
                if process.returncode is None:
                    process.kill()
            await asyncio.gather(*(process.wait() for process in self.processes))
