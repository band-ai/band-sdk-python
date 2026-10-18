"""Join the SDK-managed branches of one CrewAI Flow execution."""

from __future__ import annotations

import asyncio
from typing import Any


async def kickoff_flow(flow: Any, inputs: dict[str, Any]) -> Any:
    try:
        from crewai.flow.flow import Flow  # noqa: PLC0415 -- optional crewai extra
    except ModuleNotFoundError as error:
        if error.name != "crewai":
            raise
        return await flow.kickoff_async(inputs)

    if not isinstance(flow, Flow):
        return await flow.kickoff_async(inputs)

    from crewai.hooks import (  # noqa: PLC0415 -- optional crewai extra
        InterceptionPoint,
        register_hook,
        unregister_hook,
    )
    from crewai.hooks.contexts import StepContext  # noqa: PLC0415 -- optional crewai extra

    driver = asyncio.current_task()
    pending: set[asyncio.Task[Any]] = set()

    def forget(task: asyncio.Task[Any]) -> None:
        pending.discard(task)
        if not task.cancelled():
            task.exception()

    def retain(context: StepContext) -> None:
        if context.flow is not flow:
            return
        task = asyncio.current_task()
        if task is not None and task is not driver and task not in pending:
            pending.add(task)
            task.add_done_callback(forget)

    register_hook(InterceptionPoint.PRE_STEP, retain)
    try:
        try:
            return await flow.kickoff_async(inputs)
        finally:
            # SDK gather can finish on one branch's cancellation before its siblings.
            while pending:
                await asyncio.wait(pending)
    finally:
        unregister_hook(InterceptionPoint.PRE_STEP, retain)
