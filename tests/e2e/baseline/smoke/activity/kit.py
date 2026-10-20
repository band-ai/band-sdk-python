from __future__ import annotations

import asyncio
import os
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import BaseModel

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.types import MessageType
from tests.e2e.baseline.smoke.samples.sample_tools import LookupInput
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.tools import ToolSpec


class HeldLookup:
    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self.argument_sentinel = f"argument-{uuid4().hex}"
        self.result_sentinel = f"result-{uuid4().hex}"
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.arguments: list[LookupInput] = []
        self.spec = ToolSpec(LookupInput, self.lookup)

    async def lookup(self, args: BaseModel) -> str:
        assert isinstance(args, LookupInput)
        self.arguments.append(args)
        self.entered.set()
        async with asyncio.timeout(self.timeout_s):
            await self.release.wait()
        return self.result_sentinel


class HeldNativePeer:
    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self.argument_sentinel = f"argument-{uuid4().hex}"
        self.result_sentinel = f"result-{uuid4().hex}"
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[str] = []
        self.writers: set[asyncio.StreamWriter] = set()
        self.handlers: set[asyncio.Task[object]] = set()
        self.url = ""

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.handlers.add(task)
        self.writers.add(writer)
        try:
            async with asyncio.timeout(self.timeout_s):
                request = await reader.readuntil(b"\r\n\r\n")
                self.requests.append(request.split(b"\r\n", 1)[0].decode())
                self.entered.set()
                await self.release.wait()
                body = self.result_sentinel.encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            self.entered.set()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.handlers.discard(task)

    @property
    def command(self) -> str:
        script = f"import sys, urllib.request; sys.stdout.write(urllib.request.urlopen({self.url!r}, timeout={self.timeout_s!r}).read().decode())"
        if os.name == "nt":
            executable = sys.executable.replace("'", "''")
            escaped_script = script.replace("'", "''")
            return f"& '{executable}' -c '{escaped_script}'"
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    @asynccontextmanager
    async def running(self) -> AsyncIterator[HeldNativePeer]:
        server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/{self.argument_sentinel}"
        try:
            yield self
        finally:
            self.release.set()
            server.close()
            await server.wait_closed()
            for writer in tuple(self.writers):
                writer.close()
            await asyncio.gather(*self.handlers, return_exceptions=True)


@asynccontextmanager
async def native_activity_agent(cell: AdapterCell) -> AsyncIterator[ProvisionedAgent]:
    with TemporaryDirectory(prefix="band-activity-native-") as directory:
        adapter = ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(
                model=cell.settings.llm_models.anthropic_model,
                cwd=str(Path(directory).resolve()),
                approvals=ClaudeApprovalOptions(
                    mode="auto_accept", text_notifications=False
                ),
                custom_section="Use your native Bash or PowerShell tool to execute the user's exact test command. Report its actual output using band_send_message. Do not substitute a different tool or narrate the command.",
            ),
            emit=(),
        )
        async with running_provisioned_agent(
            adapter, cell.resources, label="activity-native"
        ) as agent:
            yield agent


async def assert_no_tool_narration(capture: ReplyCapture, agent_id: str) -> None:
    for kind in (MessageType.TOOL_CALL, MessageType.TOOL_RESULT):
        events = await capture.events(kind, sender_id=agent_id)
        events.assert_none()
