"""Compose the gateway and separately authenticated Band participants in one lifespan."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from band import Agent
from bandpap.bridge import GatewayBridge
from bandpap.gateway import PAPGateway


async def watch(agent: Agent) -> None:
    await agent.run_forever(install_signal_handlers=False)
    raise RuntimeError("A Band protocol participant stopped")


def application(
    gateway: PAPGateway,
    *,
    agents: Sequence[Agent],
    bridges: Mapping[str, GatewayBridge],
    cleanup: Callable[[], Awaitable[None]] | None = None,
) -> Starlette:
    """The host provisions identities/grants and owns the final resource cleanup.

    Each bridge mapping key is the authenticated proxy ID whose outbox it pumps.
    Shutdown cancels pumps before stopping agents and closing host resources.
    """

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    gateway.app.router.lifespan_context(gateway.app)
                )
                for agent in agents:
                    await stack.enter_async_context(agent)
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(watch(agent)) for agent in agents]
                    tasks.extend(
                        group.create_task(bridge.run(proxy=proxy))
                        for proxy, bridge in bridges.items()
                    )
                    try:
                        yield
                    finally:
                        for task in tasks:
                            task.cancel()
        finally:
            if cleanup is not None:
                await cleanup()

    return Starlette(routes=[Mount("/", app=gateway.app)], lifespan=lifespan)
