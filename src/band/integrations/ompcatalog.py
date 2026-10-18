"""Typed OMP CLI catalogs and native RPC discovery without a model turn."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import psutil
from pydantic import Field, ValidationError

from band.core.model_catalog import ModelCatalog, ModelChoice
from band.core.retention.cleanup import rejoin_owned, report_owned
from band.integrations.codex.stdio_client import HELPER_PATH
from band.integrations.codex.winspawn import (
    JobNotDrained,
    SpawnFailure,
    WindowsProcess,
    find_executable,
    process_image,
)
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


def _launcher_length(tokens: Sequence[str]) -> int | None:
    executable = Path(tokens[0]).name.casefold().removesuffix(".exe")
    if executable == "omp":
        return 1
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?w?", executable):
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token in {"-m", "-c"}:
                return index + 2 if index + 1 < len(tokens) else None
            if token in {"-W", "-X"}:
                index += 2
            elif token in {
                "-b",
                "-bb",
                "-B",
                "-d",
                "-E",
                "-i",
                "-I",
                "-O",
                "-OO",
                "-P",
                "-q",
                "-s",
                "-S",
                "-u",
                "-v",
                "-vv",
                "-x",
            } or token.startswith(("-W", "-X")):
                index += 1
            elif not token.startswith("-"):
                return index + 1
            else:
                return None
        return None
    if executable in {"node", "bun", "bunx"}:
        index = 1
        if (
            executable == "bun"
            and index < len(tokens)
            and tokens[index] in {"x", "run"}
        ):
            index += 1
        if executable in {"bun", "bunx"}:
            while index < len(tokens) and tokens[index] == "--bun":
                index += 1
        if index < len(tokens) and not tokens[index].startswith("-"):
            return index + 1
        return None
    for index, token in enumerate(tokens[1:], 1):
        if token == "launch":
            return None
        if not token.startswith("-"):
            continue
        if token == "--mode=rpc" or (
            token == "--mode" and index + 1 < len(tokens) and tokens[index + 1] == "rpc"
        ):
            return index
        return None
    return len(tokens)


def model_listing_command(command: Sequence[str]) -> tuple[str, ...]:
    """Replace native launch/mode, retaining runtime prefixes and native options."""
    length = _launcher_length(command)
    if length is None:
        raise OmpListingUnavailable("OMP CLI launcher boundary is ambiguous")
    arguments: list[str] = []
    index = length
    if index < len(command) and command[index] == "launch":
        index += 1
    elif "launch" in command[index:]:
        raise OmpListingUnavailable("OMP CLI native command boundary is ambiguous")
    while index < len(command):
        token = command[index]
        if (
            token == "--mode"
            and index + 1 < len(command)
            and command[index + 1] == "rpc"
        ):
            index += 2
        elif token == "--mode=rpc":
            index += 1
        else:
            arguments.append(token)
            index += 1
    return (*command[:length], "models", "--json", *arguments)


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
        self._group_members: set[psutil.Process] = set()
        self._group_signaled = False
        self._readers: list[asyncio.Task[bytes] | asyncio.Task[None]] = []
        self._windows_process: WindowsProcess | None = None
        self._exit: asyncio.Task[int] | None = None
        self._closing: asyncio.Task[None] | None = None

    async def _start(self) -> asyncio.subprocess.Process:
        if os.name == "nt":
            self._windows_process = owner = WindowsProcess()
            try:
                return await owner.start(
                    self._windows_command,
                    cwd=self.cwd,
                    env=self.env,
                    limit=READ_BYTES,
                )
            finally:
                self._process = owner.process
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
            start_new_session=True,
        )
        self._process = process
        self._identity = await asyncio.to_thread(self._capture_identity, process.pid)
        if self._identity is not None:
            self._group_members.add(self._identity)
        return process

    def _windows_command(self, job_name: str, status_handle: int) -> tuple[str, ...]:
        head, *rest = self.command
        if not Path(head).is_absolute():
            if os.path.dirname(head):
                found = shutil.which(head)
                if found is None and Path(head).exists():
                    found = str(Path(head).resolve())
            else:
                found = find_executable(head)
            if found is None:
                raise FileNotFoundError(head)
            head = str(Path(found).resolve())
        return (
            str(process_image()),
            "-I",
            "-S",
            str(HELPER_PATH),
            job_name,
            str(status_handle),
            "--",
            head,
            *rest,
        )

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
        except (OSError, psutil.Error, SpawnFailure):
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

    def _capture_group_members(self, group: int) -> None:
        for pid in psutil.pids():
            try:
                if os.getpgid(pid) != group:
                    continue
                identity = self._capture_identity(pid)
                if identity is not None and os.getpgid(pid) == group:
                    self._group_members.add(identity)
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue

    def _members_running(self) -> bool:
        remaining: set[psutil.Process] = set()
        for identity in self._group_members:
            current = self._capture_identity(identity.pid)
            if current is None:
                continue
            try:
                if current == identity and current.status() != psutil.STATUS_ZOMBIE:
                    remaining.add(identity)
            except psutil.NoSuchProcess:
                continue
        self._group_members = remaining
        return bool(remaining)

    def _kill_group(self) -> None:
        process = self._process
        if process is None:
            return
        deadline = time.monotonic() + PROCESS_EXIT_TIMEOUT_S
        while True:
            root = self._identity
            if root is None and process.returncode is None:
                raise OSError("Cannot confirm OMP CLI process identity")
            if process.returncode is not None or root is None or not root.is_running():
                # A reaped leader's id is reusable only once its old session is
                # empty. Never signal a new process group that inherited that id.
                try:
                    os.kill(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    if self._members_running():
                        raise OSError(
                            "OMP CLI group identity changed with live members"
                        )
                    return
            self._capture_group_members(process.pid)
            alive = self._members_running()
            if self._group_signaled and not alive:
                return
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                if not self._members_running():
                    return
            else:
                if alive:
                    self._group_signaled = True
            # Keep concrete identities even when a later global PID snapshot
            # omits them. Signal delivery alone does not prove physical exit.
            self._capture_group_members(process.pid)
            if self._group_signaled and not self._members_running():
                return
            if time.monotonic() >= deadline:
                raise OSError("OMP CLI process group did not exit")
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

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
        except (OSError, psutil.Error, SpawnFailure):
            process = self._process
        for reader in self._readers:
            reader.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)
        self._readers = [
            asyncio.create_task(self._drain(stream))
            for stream in ((process.stdout, process.stderr) if process else ())
            if stream is not None
        ]
        errors: list[Exception] = []
        if self._windows_process is not None:
            try:
                await self._windows_process.close(PROCESS_EXIT_TIMEOUT_S)
            except (OSError, SpawnFailure, JobNotDrained) as error:
                errors.append(error)
        elif process is not None:
            try:
                await asyncio.to_thread(self._kill_group)
            except (OSError, psutil.Error) as error:
                errors.append(error)
        if process is None:
            if errors:
                raise ExceptionGroup("OMP CLI cleanup failed", errors)
            return
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
