"""Control and HTTP-boundary peers for scheduler contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from band.agent import Agent
from band.core.exceptions import InputNotAccepted
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext, ExecutionState
from tests.core.agentlifecycle.kit import make_runtime
from tests.platform.settlement.kit import link_over
from tests.runtime.conftest import Waiting
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
    *,
    started: bool = False,
    batching: bool = True,
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
                config=session_config(batching=batching, retries=1),
                agent_id=AGENT_ID,
                on_execute_batch=callbacks["on_execute_batch"],
                on_message_control=callbacks["on_message_control"],
                is_message_control=callbacks["is_message_control"],
            )
            try:
                if started:
                    await ctx.start()
                    await Waiting.until(lambda: ctx.state is ExecutionState.IDLE)
                await ctx.on_event(peer.post("expired"))
                yield peer, ctx, callbacks["on_message_control"]
            finally:
                await ctx.stop(timeout=STOP_SECONDS)


class IngressAdapter(DecliningAdapter):
    """Hold fallback and a later control at observable adapter boundaries."""

    probe_id = "probe"

    def __init__(self, *, recognized: bool = True, refuse: bool = False) -> None:
        super().__init__(recognized=recognized, refuse=refuse)
        self.fallbacks: asyncio.Queue[Any] = asyncio.Queue()
        self.release_fallback = asyncio.Event()
        self.probe_entered = asyncio.Event()
        self.release_probe = asyncio.Event()

    def is_control_message(self, msg: Any) -> bool:
        return msg.id == self.probe_id or super().is_control_message(msg)

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        if msg.id == self.probe_id:
            self.probe_entered.set()
            await self.release_probe.wait()
            return True
        return await super().try_handle_control(msg, tools)

    async def on_event(self, inp: Any) -> None:
        self.fallbacks.put_nowait(inp.msg)
        await self.release_fallback.wait()
        await super().on_event(inp)


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


class LateDecliningAdapter(DecliningAdapter):
    """Return a delayed decline after native cancellation, then accept reoffering."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.offers: list[str] = []

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        self.offers.append(msg.id)
        if self.entered.is_set():
            return True
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        return await super().try_handle_control(msg, tools)
