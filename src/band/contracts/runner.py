"""Qualify and invoke the retained local runner without importing POSIX modules."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

from band.contracts.errors import ContractRuntimeUnsupported
from band.contracts.models import RuntimeConfig
from band.contracts.process import ManagedProcess
from band.contracts.wire import JsonObject, decode_json


class QualifiedRunner:
    def __init__(self, configuration: RuntimeConfig) -> None:
        self.configuration = configuration.model_copy(deep=True)
        self._qualified = False
        self._qualification = asyncio.Lock()

    async def check(self) -> None:
        if sys.platform != "linux":
            raise ContractRuntimeUnsupported(
                "Local contract execution requires qualified Linux and Python 3.12"
            )
        async with self._qualification:
            if self._qualified:
                return
            probe = self.process(
                (
                    str(self.configuration.python),
                    "-I",
                    "-c",
                    "import json,sys;sys.stdout.write(json.dumps({'version':list(sys.version_info[:2]),'platform':sys.platform}))",
                ),
            )
            result = await probe.run()
            facts = decode_json(result.stdout)
            if facts.get("platform") != "linux" or facts.get("version") != [3, 12]:
                raise ContractRuntimeUnsupported(
                    "Local contract execution requires qualified Linux and Python 3.12"
                )
            self._qualified = True

    def command(self, module: str, arguments: tuple[str, ...]) -> tuple[str, ...]:
        return (str(self.configuration.python), "-I", "-m", module, *arguments)

    def process(
        self,
        command: tuple[str, ...],
        *,
        ready_event: str | None = None,
        qualify: bool = False,
    ) -> ManagedProcess:
        return ManagedProcess(
            command,
            options=self.configuration,
            ready_event=ready_event,
            cwd=self.configuration.cwd,
            environment=self.configuration.environment,
            prepare=self.check if qualify else None,
        )

    async def execute(
        self,
        module: str,
        arguments: tuple[str, ...],
        *,
        allowed: tuple[int, ...] = (0,),
        decoder: Callable[[bytes], JsonObject] = decode_json,
    ) -> JsonObject:
        owner = self.process(self.command(module, arguments), qualify=True)
        result = await owner.run(allowed=allowed)
        return decoder(result.stdout)
