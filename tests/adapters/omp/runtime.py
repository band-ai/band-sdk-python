"""Hold an external prompt at its callback boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import JsonValue

from band.adapters.omp import OmpAdapter
from band.integrations.omprpc.client import COMPLETION_TIMEOUT_S, OmpScheduler
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)


@dataclass
class PromptCallbacks:
    event: Callable[[OmpEvent], Awaitable[None]]
    tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]]
    ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]]


class PendingRuntime:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.yielded = asyncio.Event()
        self.finish = asyncio.Event()
        self.closed = False
        self.reply = True
        self.completion_timeout_s = COMPLETION_TIMEOUT_S
        self.callbacks: asyncio.Future[PromptCallbacks] = (
            asyncio.get_running_loop().create_future()
        )

    async def start(self) -> None:
        return None

    async def request(
        self, command: str, **_parameters: JsonValue
    ) -> dict[str, JsonValue]:
        match command:
            case OmpCommandName.GET_STATE:
                return {
                    "sessionId": "native-session",
                    "sessionFile": None,
                    "isSettled": True,
                }
            case OmpCommandName.SET_ASK_DIALOG:
                return {"enabled": True}
            case OmpCommandName.SET_HOST_TOOLS:
                return {}
            case _:
                raise ValueError(f"Unsupported fake runtime command: {command}")

    async def prompt(
        self,
        _message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
        schedule_admission: OmpScheduler | None = None,
    ) -> None:
        if schedule_admission is None:
            await self._write_prompt(_message)
        else:
            await schedule_admission(lambda: self._write_prompt(_message))
        await self._prompt_body(
            _message, on_event=on_event, on_tool=on_tool, on_ui=on_ui
        )

    async def _write_prompt(self, _message: str) -> None:
        return None

    async def _prompt_body(
        self,
        _message: str,
        *,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        if not self.callbacks.done():
            self.callbacks.set_result(PromptCallbacks(on_event, on_tool, on_ui))
        self.yielded.set()
        await self.finish.wait()
        if self.reply:
            await on_tool(
                OmpHostToolCall(
                    id="host-call",
                    toolCallId="reply",
                    toolName="band_send_message",
                    arguments={"content": "Background work finished"},
                )
            )

    def raise_if_retirement_unproven(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


AdapterFactory = Callable[..., OmpAdapter]
