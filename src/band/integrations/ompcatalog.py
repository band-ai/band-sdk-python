"""Typed OMP CLI catalogs and native RPC discovery without a model turn."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence

import psutil
from pydantic import Field, ValidationError

from band.core.model_catalog import ModelCatalog, ModelChoice
from band.core.retention.cleanup import rejoin_owned, report_owned
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import OMP_REASSEMBLED_BYTES, OmpState, OmpWireModel

READ_BYTES = 64 * 1024
PROCESS_EXIT_TIMEOUT_S = 2.0


class OmpListingUnavailable(RuntimeError):
    """The optional CLI command did not supply a usable JSON model catalog."""


class OmpCliModel(OmpWireModel):
    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    selector: str | None = None
    name: str | None = None
    reasoning: bool | None = None
    thinking: list[str] | None = None

    def choice(self) -> ModelChoice:
        efforts = (
            tuple(self.thinking or ())
            if "thinking" in self.model_fields_set or self.reasoning is False
            else None
        )
        return ModelChoice(
            id=self.selector or f"{self.provider}/{self.id}",
            label=self.name or self.id,
            provider=self.provider,
            efforts=efforts,
        )


class OmpCliCatalog(OmpWireModel):
    models: list[OmpCliModel]

    def catalog(self) -> ModelCatalog:
        return ModelCatalog(models=tuple(model.choice() for model in self.models))


class OmpThinking(OmpWireModel):
    efforts: list[str]
    defaultLevel: str | None = None


class OmpCatalogModel(OmpWireModel):
    """A catalog record, separate from the small current-session OmpModel."""

    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    name: str | None = None
    reasoning: bool | None = None
    thinking: OmpThinking | None = None

    def choice(self) -> ModelChoice:
        if self.thinking is not None:
            efforts = tuple(self.thinking.efforts)
        elif "thinking" in self.model_fields_set or self.reasoning is False:
            efforts = ()
        else:
            efforts = None
        return ModelChoice(
            id=f"{self.provider}/{self.id}",
            label=self.name or self.id,
            provider=self.provider,
            efforts=efforts,
            default_effort=self.thinking.defaultLevel if self.thinking else None,
        )


class OmpNativeCatalog(OmpWireModel):
    models: list[OmpCatalogModel]


class OmpThinkingLevels(OmpWireModel):
    levels: list[str]


async def native_catalog(runtime: OmpRpcClient) -> ModelCatalog:
    """Read full native model records and the live model's selectable levels."""
    offered = OmpNativeCatalog.model_validate(
        await runtime.request("get_available_models")
    )
    state = OmpState.model_validate(await runtime.request("get_state"))
    current = (
        f"{state.model.provider}/{state.model.id}" if state.model is not None else None
    )
    choices = tuple(model.choice() for model in offered.models)
    if current is not None and any(choice.id == current for choice in choices):
        levels = OmpThinkingLevels.model_validate(
            await runtime.request("get_available_thinking_levels")
        )
        choices = tuple(
            choice.model_copy(update={"efforts": tuple(levels.levels)})
            if choice.id == current
            else choice
            for choice in choices
        )
    return ModelCatalog(models=choices, current_model=current)


def model_listing_command(command: Sequence[str]) -> tuple[str, ...]:
    """Replace native mode in place, preserving executable and launcher arguments."""
    tokens = list(command)
    for index, token in enumerate(tokens):
        if token == "--mode" and index + 1 < len(tokens) and tokens[index + 1] == "rpc":
            return (*tokens[:index], "models", "--json", *tokens[index + 2 :])
        if token == "--mode=rpc":
            return (*tokens[:index], "models", "--json", *tokens[index + 1 :])
    return (*tokens, "models", "--json")


class OmpModelListing:
    """One CLI transport; the adapter's HarnessProbe owns its close callback."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None,
        cwd: str | None,
    ) -> None:
        self.command = tuple(command)
        self.env = None if env is None else {**os.environ, **env}
        self.cwd = cwd
        self._spawn: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._identity: psutil.Process | None = None
        self._readers: list[asyncio.Task[bytes] | asyncio.Task[None]] = []
        self._descendants: set[psutil.Process] = set()
        self._exit: asyncio.Task[int] | None = None
        self._closing: asyncio.Task[None] | None = None

    async def _start(self) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
        )
        self._process = process
        self._identity = await asyncio.to_thread(self._capture_identity, process.pid)
        return process

    @staticmethod
    def _capture_identity(pid: int) -> psutil.Process | None:
        try:
            process = psutil.Process(pid)
            process.create_time()
            return process
        except psutil.NoSuchProcess:
            return None

    async def read(self) -> ModelCatalog:
        self._spawn = asyncio.create_task(self._start())
        try:
            process = await asyncio.shield(self._spawn)
        except (OSError, psutil.Error):
            raise OmpListingUnavailable(
                "OMP CLI model command is unavailable"
            ) from None
        assert process.stdout is not None and process.stderr is not None
        stdout = asyncio.create_task(self._read_output(process.stdout))
        stderr = asyncio.create_task(self._drain(process.stderr))
        self._readers.extend((stdout, stderr))
        output = await asyncio.shield(stdout)
        await asyncio.shield(stderr)
        await process.wait()
        if process.returncode != 0:
            raise OmpListingUnavailable("OMP CLI model command did not succeed")
        try:
            return OmpCliCatalog.model_validate_json(output).catalog()
        except ValidationError:
            raise OmpListingUnavailable(
                "OMP CLI returned no valid model catalog"
            ) from None

    @staticmethod
    async def _read_output(stream: asyncio.StreamReader) -> bytes:
        output = bytearray()
        while chunk := await stream.read(READ_BYTES):
            if len(output) + len(chunk) > OMP_REASSEMBLED_BYTES:
                raise OmpListingUnavailable(
                    "OMP CLI model catalog exceeds the output limit"
                )
            output.extend(chunk)
        return bytes(output)

    @staticmethod
    async def _drain(stream: asyncio.StreamReader) -> None:
        while await stream.read(READ_BYTES):
            pass

    @staticmethod
    def _process_is_alive(process: psutil.Process) -> bool:
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def _kill_tree(self) -> None:
        root = self._identity
        errors: list[Exception] = []
        try:
            if root is not None and self._process_is_alive(root):
                for child in root.children(recursive=True):
                    child.create_time()
                    self._descendants.add(child)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            errors.append(error)
        # Stop the launcher before waiting on children, so it cannot keep
        # creating work or prevent their physical exit from settling cleanup.
        processes = [*self._descendants]
        if root is not None:
            processes.insert(0, root)
        for process in processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                errors.append(error)
        deadline = time.monotonic() + PROCESS_EXIT_TIMEOUT_S
        alive = set(self._descendants)
        while alive:
            remaining: set[psutil.Process] = set()
            for process in alive:
                try:
                    if self._process_is_alive(process):
                        remaining.add(process)
                except psutil.Error as error:
                    errors.append(error)
                    remaining.add(process)
            alive = remaining
            if not alive or errors or time.monotonic() >= deadline:
                break
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        if alive:
            errors.append(OSError("OMP CLI descendants did not exit"))
        if errors:
            raise ExceptionGroup("OMP CLI process-tree cleanup failed", errors)

    async def close(self) -> None:
        self._closing = rejoin_owned(
            self._closing,
            self._close,
            label="OMP CLI catalog cleanup",
            diagnostic_details=False,
        )
        await report_owned(self._closing, None)

    async def _close(self) -> None:
        if self._spawn is None:
            return
        try:
            process = await asyncio.shield(self._spawn)
        except (OSError, psutil.Error):
            process = self._process
        if process is None:
            return
        for reader in self._readers:
            reader.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)
        self._readers = [
            asyncio.create_task(self._drain(stream))
            for stream in (process.stdout, process.stderr)
            if stream is not None
        ]
        errors: list[Exception] = []
        try:
            await asyncio.to_thread(self._kill_tree)
        except (psutil.Error, ExceptionGroup) as error:
            errors.append(error)
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError as error:
                errors.append(error)
        if self._exit is None:
            self._exit = asyncio.create_task(process.wait())
        try:
            await asyncio.wait_for(asyncio.shield(self._exit), PROCESS_EXIT_TIMEOUT_S)
        except TimeoutError:
            errors.append(OSError("OMP CLI process did not exit"))
        if errors:
            raise ExceptionGroup("OMP CLI cleanup failed", errors)
        await asyncio.gather(*self._readers)
        self._readers.clear()
