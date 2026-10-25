"""Gated callback handlers and transcript projections for real RPC tests."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from pydantic import JsonValue

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpCommandName,
    OmpContent,
    OmpContentKind,
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.mailbox import RoomMailbox
from tests.adapters.hold import Hold
from tests.integrations.omprpc import callbackpeer
from tests.integrations.omprpc.callbackpeer import (
    CALLBACK_ID,
    NEW_RESULT,
    CallbackKind,
)

ROOM = "callback-identity"


async def held_prompt_admission(
    mailbox: RoomMailbox, hold: Hold, write: Callable[[], Awaitable[None]]
) -> None:
    async def admitted() -> None:
        await hold.wait()
        await write()

    await mailbox.ask(ROOM, admitted)


class CallbackGate:
    """Hold one handler, including its cancellation cleanup, until released."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.task: asyncio.Task[object] | None = None

    async def result(self) -> str:
        self.task = asyncio.current_task()
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        return self.value

    async def finish(self) -> None:
        self.release.set()
        assert self.task is not None
        await self.task


class HeldCallbacks:
    """Serve the explicitly supplied gates through the public callback API."""

    def __init__(self, *gates: CallbackGate) -> None:
        self.gates = deque(gates)

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(
            content=[
                OmpContent(
                    type=OmpContentKind.TEXT, text=await self.gates.popleft().result()
                )
            ]
        )

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(value=await self.gates.popleft().result())


class ImmediateCallbacks:
    """Complete without suspending so eager tasks reach result validation inline."""

    def __init__(self) -> None:
        self.completed = asyncio.Event()

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        self.completed.set()
        return OmpToolResult(
            content=[OmpContent(type=OmpContentKind.TEXT, text=NEW_RESULT)]
        )

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        self.completed.set()
        return OmpUIResponse(value=NEW_RESULT)


@asynccontextmanager
async def callback_client(
    kind: CallbackKind, scheduled: bool, *, transcript: Path | None = None
) -> AsyncGenerator[OmpRpcClient]:
    mailbox = RoomMailbox()
    command = [sys.executable, "-u", "-m", callbackpeer.__name__, kind]
    if transcript is not None:
        command.extend(("--transcript", str(transcript)))
    client = OmpRpcClient(command)
    try:
        await client.start()
        if scheduled:
            client.schedule_callback = partial(mailbox.ask, ROOM)
        yield client
    finally:
        try:
            await client.close()
        finally:
            await mailbox.close(ROOM)


def received_commands(transcript: Path) -> list[OmpCommandName]:
    frames = [
        json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()
    ]
    return [
        OmpCommandName(frame["type"])
        for frame in frames
        if frame["type"] in (OmpCommandName.PROMPT, OmpCommandName.SET_MODEL)
    ]


def callback_results(frames: list[dict[str, JsonValue]]) -> list[str]:
    results: list[str] = []
    for frame in frames:
        if frame.get("id") != CALLBACK_ID:
            continue
        match frame["type"]:
            case OmpFrameType.HOST_TOOL_RESULT:
                result = frame["result"]
                assert isinstance(result, dict)
                content = result["content"]
                assert isinstance(content, list)
                for part in content:
                    assert isinstance(part, dict) and isinstance(part["text"], str)
                    results.append(part["text"])
            case OmpFrameType.UI_RESPONSE:
                value = frame["value"]
                assert isinstance(value, str)
                results.append(value)
    return results
