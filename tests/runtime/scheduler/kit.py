"""Control and HTTP-boundary peers for scheduler contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from band.agent import Agent
from band.core.exceptions import InputNotAccepted
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from tests.core.agentlifecycle.kit import make_runtime
from tests.platform.settlement.kit import link_over
from tests.runtime.delivery.kit import RestPeer, session_config
from tests.runtime.helpers import AGENT_ID, ROOM_ID, STOP_SECONDS, rest_client_over

# The host's control handler, as the execution context was given it.
ControlCallback = Callable[[ExecutionContext, PlatformEvent], Awaitable[bool]]


class DecliningAdapter:
    supports_batch_input = True

    def __init__(self, *, recognized: bool = True, refuse: bool = False) -> None:
        self.ordinary: list[str] = []
        self.recognized = recognized
        self.refuse = refuse

    async def on_started(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def on_stopped(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def on_cleanup(self, *args: Any, **kwargs: Any) -> None:
        pass

    def is_control_message(self, msg: Any) -> bool:
        return self.recognized and msg.content == "expired"

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        await tools.send_event("That request expired", "error")
        return False

    async def on_event(self, inp: Any) -> None:
        self.ordinary.append(inp.msg.id)
        self.recognized = True
        if self.refuse:
            raise InputNotAccepted("busy")

    async def on_messages(self, inp: Any) -> None:
        self.ordinary.extend(msg.id for msg in inp.messages)


@asynccontextmanager
async def declining_room(
    adapter: DecliningAdapter,
) -> AsyncIterator[tuple[RestPeer, ExecutionContext, ControlCallback]]:
    peer = RestPeer()
    runtime = make_runtime()
    runtime.agent_id = AGENT_ID
    async with Agent(runtime=runtime, adapter=adapter):
        callbacks = runtime.start.await_args.kwargs
        async with rest_client_over(peer.answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                callbacks["on_execute"],
                config=session_config(batching=False),
                agent_id=AGENT_ID,
                on_execute_batch=callbacks.get("on_execute_batch"),
                on_message_control=callbacks["on_message_control"],
                is_message_control=callbacks["is_message_control"],
            )
            try:
                await ctx.on_event(peer.post("expired"))
                yield peer, ctx, callbacks["on_message_control"]
            finally:
                await ctx.stop(timeout=STOP_SECONDS)


class WaitingAdapter(DecliningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        """Decline like the parent, holding only the first offer until released."""
        declined = await super().try_handle_control(msg, tools)
        if not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        return declined
