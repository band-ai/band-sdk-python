"""Run one turn through a ``FrameworkAdapter``.

Claim/mark/drain stay in the invoker; this is only the turn boundary:
``AgentInput`` → ``RunResult``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from band.core.turn.observing import ObservingTools
from band.core.contracts import RunResult
from band.core.protocols import (
    AgentToolsProtocol,
    CancellationToken,
    EventSink,
    FrameworkAdapter,
    RunContext,
)
from band.core.run.cancellation import NeverCancelled
from band.core.run.context import SimpleRunContext
from band.core.types import AgentInput


async def run_adapter_turn(
    adapter: FrameworkAdapter,
    inp: AgentInput,
    *,
    context: RunContext,
) -> RunResult:
    """Wrap tools in ``ObservingTools``, call ``handle_turn``, return receipt.

    The proxy carries the turn's sink and cancellation (``turn_context``) because
    ``on_message`` has no parameters for them.
    """
    observing = ObservingTools(_inner=context.tools, turn=context)
    # __getattr__ forwarders are invisible to static analysis.
    inp = replace(inp, tools=cast(AgentToolsProtocol, observing))
    await adapter.handle_turn(inp)
    return RunResult(
        usage=None,
        delivery=observing.receipt,
    )


async def run_oneshot_turn(
    adapter: FrameworkAdapter,
    inp: AgentInput,
    *,
    events: EventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> RunResult:
    """Build the turn's context and hand ``inp`` to the adapter."""
    tools: AgentToolsProtocol = inp.tools
    context = SimpleRunContext(
        tools=tools,
        cancellation=cancellation or NeverCancelled(),
    )
    if events is not None:
        context.events = events
    return await run_adapter_turn(adapter, inp, context=context)
