"""Join the SDK-managed branches of one CrewAI Flow execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crewai.hooks.contexts import StepContext


async def kickoff_flow(flow: Any, inputs: dict[str, Any]) -> Any:
    try:
        from crewai.flow.flow import Flow  # noqa: PLC0415 -- optional crewai extra
    except ModuleNotFoundError as error:
        if error.name != "crewai":
            raise
        return await flow.kickoff_async(inputs)

    if not isinstance(flow, Flow):
        return await flow.kickoff_async(inputs)

    from crewai.hooks.dispatch import (  # noqa: PLC0415 -- optional crewai extra
        InterceptionPoint,
        get_scoped_hooks,
        register_scoped,
        scoped_hooks,
    )

    driver = asyncio.current_task()
    pending: set[asyncio.Task[Any]] = set()

    def forget(task: asyncio.Task[Any]) -> None:
        pending.discard(task)
        if not task.cancelled():
            task.exception()

    def retain(context: StepContext) -> None:
        if context.flow is None:
            return
        task = asyncio.current_task()
        if task is not None and task is not driver and task not in pending:
            pending.add(task)
            task.add_done_callback(forget)

    inherited_hooks = {point: get_scoped_hooks(point) for point in InterceptionPoint}
    with scoped_hooks(inherited_hooks):
        register_scoped(InterceptionPoint.PRE_STEP, retain)
        try:
            return await flow.kickoff_async(inputs)
        finally:
            # SDK gather can finish on one branch's cancellation before its siblings.
            while pending:
                await asyncio.wait(pending)
