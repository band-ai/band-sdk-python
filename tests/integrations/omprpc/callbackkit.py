"""Gated callback handlers and transcript projections for real RPC tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from pydantic import JsonValue

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from band.runtime.mailbox import RoomMailbox
from tests.integrations.omprpc import callbackpeer
from tests.integrations.omprpc.callbackpeer import (
    CALLBACK_ID,
    NEW_RESULT,
    OLD_RESULT,
    CallbackKind,
)

ROOM = "callback-identity"


class HeldCallbacks:
    """Hold cancelled work until replacement identity has been admitted."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.replacement_entered = asyncio.Event()
        self.release_replacement = asyncio.Event()
        self.tasks: list[asyncio.Task[object]] = []

    async def result(self) -> str:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.append(task)
        if len(self.tasks) == 1:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
            return OLD_RESULT
        self.replacement_entered.set()
        await self.release_replacement.wait()
        return NEW_RESULT

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        return OmpToolResult(
            content=[OmpContent(type="text", text=await self.result())]
        )

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        return OmpUIResponse(value=await self.result())

    async def settle_old(self) -> None:
        self.release.set()
        await asyncio.gather(self.tasks[0], return_exceptions=True)

    async def close(self) -> None:
        self.release.set()
        self.release_replacement.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class ImmediateCallbacks:
    """Complete without suspending so eager tasks reach result validation inline."""

    def __init__(self) -> None:
        self.completed = asyncio.Event()

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        self.completed.set()
        return OmpToolResult(content=[OmpContent(type="text", text=NEW_RESULT)])

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        self.completed.set()
        return OmpUIResponse(value=NEW_RESULT)


class CallbackMailbox:
    def __init__(self) -> None:
        self.mailbox = RoomMailbox()
        self.admitted: asyncio.Queue[None] = asyncio.Queue()

    async def schedule(self, run: Callable[[], Awaitable[None]]) -> None:
        self.admitted.put_nowait(None)
        await self.mailbox.ask(ROOM, run)

    async def close(self) -> None:
        await self.mailbox.close(ROOM)


@asynccontextmanager
async def callback_client(
    kind: CallbackKind, scheduled: bool
) -> AsyncGenerator[tuple[OmpRpcClient, CallbackMailbox]]:
    mailbox = CallbackMailbox()
    client = OmpRpcClient((sys.executable, "-u", "-m", callbackpeer.__name__, kind))
    try:
        await client.start()
        if scheduled:
            client.schedule_callback = mailbox.schedule
        try:
            yield client, mailbox
        finally:
            await mailbox.close()
    finally:
        await client.close()


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
