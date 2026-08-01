"""Run one turn through a ``FrameworkAdapter``.

Claim/mark/drain stay in the invoker; this is only the turn boundary:
``AgentInput`` → ``RunResult``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from band.core.backends.observing import ObservingTools
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
    """Wrap tools for delivery/sink, call ``adapter.handle_turn``, return receipt.

    ``on_message`` takes neither the turn's sink nor its token, so the per-turn
    proxy carries the whole context: tool-first adapters (ACP RoomTurnEmitter)
    dual-write TurnEvents onto the same sink ``AgentStream.observe`` reads, and
    façade adapters running their own inner loop cancel with the outer turn.
    Both reach it via ``turn_context()``.
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
