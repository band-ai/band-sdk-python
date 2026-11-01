"""Drive the real early hook and exact per-engine ContextVar identity."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band.integrations.parlant.sessiontools import (
    capture_engine_origin,
    set_session_tools,
)


async def bind_engine_tools(session_id: str, tools: Any) -> Any:
    from parlant.core.engines.alpha.engine_context import EngineContext  # noqa: PLC0415 -- optional provider dependency
    from parlant.core.engines.alpha.entity_context import EntityContext  # noqa: PLC0415 -- optional provider dependency
    from parlant.core.engines.alpha.hooks import EngineHooks  # noqa: PLC0415 -- optional provider dependency

    set_session_tools(session_id, tools)
    context = cast(
        EngineContext, SimpleNamespace(session=SimpleNamespace(id=session_id))
    )
    hooks = EngineHooks(on_acknowledging=[capture_engine_origin])
    await hooks.call_on_acknowledging(context)
    EntityContext.set(context)
    return context


def activate_engine(context: Any) -> None:
    from parlant.core.engines.alpha.entity_context import EntityContext  # noqa: PLC0415 -- optional provider dependency

    EntityContext.set(context)


@asynccontextmanager
async def running_engine(session_id: str, tools: Any) -> AsyncIterator[Any]:
    ready = asyncio.get_running_loop().create_future()
    release = asyncio.Event()

    async def process() -> None:
        engine = await bind_engine_tools(session_id, tools)
        ready.set_result(engine)
        await release.wait()

    task = asyncio.create_task(process())
    try:
        engine = await ready
        activate_engine(engine)
        yield engine
    finally:
        release.set()
        await task
