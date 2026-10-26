"""Inject external runtime and platform failures."""

from __future__ import annotations

import asyncio

from band.core.delivery import DeliveryFailedError
from band.integrations.ompwire import (
    OmpCommandName,
    OmpHostToolCall,
)
from band.runtime.tools import ToolCallOutcome
from band.testing import FakeAgentTools
from tests.adapters.omp.lifecyclekit import BoomCloseRuntime
from tests.adapters.omp.runtime import PendingRuntime


class CancelDuringFailureTools(FakeAgentTools):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_failure(self, failure):
        self.entered.set()
        await self.release.wait()
        return await super().send_failure(failure)


class FetchBoomTools(FakeAgentTools):
    async def fetch_room_context(self, **_kwargs):
        raise RuntimeError("context unavailable")


class FailedDeliveryTools(FakeAgentTools):
    async def execute_tool_call_structured(self, tool_name, arguments):
        return ToolCallOutcome(
            value={"error": "delivery rejected"},
            ok=False,
            error_message="delivery rejected",
        )


class BoomCloseDeliveryRuntime(BoomCloseRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise DeliveryFailedError(RuntimeError("delivery rejected"))


class HangAfterPromptRuntime(PendingRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.prompted = False
        self.hung = asyncio.Event()

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await on_tool(
            OmpHostToolCall(
                id="host-call",
                toolCallId="reply",
                toolName="band_send_message",
                arguments={"content": "Already replied"},
            )
        )
        self.prompted = True

    async def request(self, command, **_parameters):
        if command == OmpCommandName.GET_STATE and self.prompted:
            self.hung.set()
            await asyncio.Event().wait()
        return await super().request(command, **_parameters)


class BoomGetStateAfterReplyRuntime(HangAfterPromptRuntime):
    async def request(self, command, **_parameters):
        if command == OmpCommandName.GET_STATE and self.prompted:
            raise RuntimeError("state failed")
        return await super().request(command, **_parameters)


class LeftoverHostToolRuntime(BoomGetStateAfterReplyRuntime):
    def __init__(self, *_args, **_kwargs):
        super().__init__(*_args, **_kwargs)
        self.leftover: asyncio.Task | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        await super().prompt(_message, on_event=on_event, on_tool=on_tool, on_ui=on_ui)

        async def leftover_call() -> None:
            self.started.set()
            await self.release.wait()
            await on_tool(
                OmpHostToolCall(
                    id="late-call",
                    toolCallId="late",
                    toolName="band_send_message",
                    arguments={"content": "Late reply"},
                )
            )

        self.leftover = asyncio.create_task(leftover_call())


class CrashPromptRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise RuntimeError("provider crashed")


class DeliveryCrashRuntime(PendingRuntime):
    async def prompt(self, _message, *, on_event, on_tool, on_ui):
        raise DeliveryFailedError(RuntimeError("delivery rejected"))
