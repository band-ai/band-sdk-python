"""Async owners of the retained contract, author and program service processes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Self

from band.contracts.errors import ContractProcessError, ContractProtocolError
from band.contracts.models import ProcessResult, RuntimeConfig
from band.contracts.runner import QualifiedRunner
from band.contracts.wire import JsonObject


class ManagedService:
    """Expose readiness and confirmed closure while the child owns durable state."""

    def __init__(
        self,
        *,
        runtime: RuntimeConfig,
        module: str,
        arguments: tuple[str, ...],
        ready_event: str,
    ) -> None:
        self._runner = QualifiedRunner(runtime)
        self._owner = self._runner.process(
            self._runner.command(module, arguments),
            ready_event=ready_event,
            qualify=True,
        )

    @property
    def running(self) -> bool:
        return self._owner.running

    @property
    def pid(self) -> int | None:
        return self._owner.pid

    @property
    def result(self) -> ProcessResult | None:
        return self._owner.result

    @property
    def events(self) -> list[JsonObject]:
        """Return bounded diagnostics; the child's journal remains authoritative."""
        return self._owner.events

    async def start(self) -> JsonObject:
        return await self._owner.start()

    async def wait(self) -> ProcessResult:
        result = await self._owner.wait()
        self._check_exit(result)
        return result

    async def aclose(self) -> None:
        result = await self._owner.aclose()
        if result is not None:
            self._check_exit(result)

    @staticmethod
    def _check_exit(result: ProcessResult) -> None:
        if result.forced or result.returncode != 0:
            raise ContractProcessError(
                f"Contract service exited with status {result.returncode}"
            )

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


class ContractService(ManagedService):
    """Run a configured reference authority or supervised participant service."""

    def __init__(self, config: Path, *, runtime: RuntimeConfig) -> None:
        super().__init__(
            runtime=runtime,
            module="remote_agent.cli",
            arguments=("serve", str(config)),
            ready_event="service_ready",
        )

    @staticmethod
    async def check_config(config: Path, *, runtime: RuntimeConfig) -> JsonObject:
        return await QualifiedRunner(runtime).execute(
            "remote_agent.cli", ("check-config", str(config))
        )


class ContractAuthorService(ManagedService):
    """Run the dedicated author with its original exclusive journal ownership."""

    def __init__(self, config: Path, *, runtime: RuntimeConfig) -> None:
        self._config = config
        self._maintenance = asyncio.Lock()
        super().__init__(
            runtime=runtime,
            module="remote_agent.band.author_service",
            arguments=("--config", str(config)),
            ready_event="protocol_author_ready",
        )

    async def start(self) -> JsonObject:
        async with self._maintenance:
            return await super().start()

    async def _operate(
        self, arguments: tuple[str, ...], *, allowed: tuple[int, ...] = (0,)
    ) -> JsonObject:
        async with self._maintenance:
            if self._owner.active:
                raise ContractProcessError(
                    "Close the author service before exclusive held-completion maintenance"
                )
            return await self._runner.execute(
                "remote_agent.band.author_service",
                ("--config", str(self._config), *arguments),
                allowed=allowed,
            )

    async def list_held(self) -> JsonObject:
        return await self._operate(("--list-held",))

    async def retry_held(self, job: str, *, attempt: int) -> JsonObject:
        if not job or type(attempt) is not int or attempt < 0:
            raise ValueError("Provide the exact held job and nonnegative attempt")
        result = await self._operate(
            ("--retry-held", job, "--attempt", str(attempt)), allowed=(0, 2)
        )
        if (
            result.get("job") != job
            or result.get("attempt") != attempt
            or type(result.get("acknowledged")) is not bool
        ):
            raise ContractProtocolError(
                "Author maintenance did not identify the requested held completion"
            )
        return result


class ContractProgramService(ManagedService):
    """Run the qualified program host with the retained registry and stop barriers."""

    def __init__(self, config: Path, *, runtime: RuntimeConfig) -> None:
        super().__init__(
            runtime=runtime,
            module="remote_agent.band.serve_programs",
            arguments=("--config", str(config)),
            ready_event="program_host_ready",
        )
