"""Run one turn through an adapter (or a test turn-runner).

Claim/mark/drain stay in the invoker; this is only the turn boundary:
``AgentInput`` → ``RunResult``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TypeAlias, cast, runtime_checkable

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
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput

# ``SimpleAdapter`` implements ``FrameworkAdapter``; both spellings appear at
# call sites (Agent constructor, tests), so the union keeps them explicit.
Adapter: TypeAlias = FrameworkAdapter | SimpleAdapter[object]


@runtime_checkable
class TurnRunner(Protocol):
    """Prepared-turn executor for tests that drive a bare native loop."""

    async def run(self, inp: AgentInput, *, context: RunContext) -> RunResult: ...


TurnTarget: TypeAlias = Adapter | TurnRunner


async def run_adapter_turn(
    adapter: Adapter,
    inp: AgentInput,
    *,
    context: RunContext,
) -> RunResult:
    """Wrap tools for delivery/sink, call ``adapter.on_event``, return receipt.

    ``on_message`` takes neither the turn's sink nor its token, so the per-turn
    proxy carries the whole context: tool-first adapters (ACP RoomTurnEmitter)
    dual-write TurnEvents onto the same sink ``AgentStream.observe`` reads, and
    façade adapters running their own inner loop cancel with the outer turn.
    Both reach it via ``turn_context()``.
    """
    observing = ObservingTools(_inner=context.tools, turn=context)
    # __getattr__ forwarders are invisible to static analysis.
    inp = replace(inp, tools=cast(AgentToolsProtocol, observing))
    await adapter.on_event(inp)
    return RunResult(
        usage=None,
        delivery=observing.receipt,
    )


async def execute_turn(
    target: TurnTarget,
    inp: AgentInput,
    *,
    context: RunContext,
) -> RunResult:
    """Dispatch: adapters via ``on_event``, test runners via ``.run``."""
    if isinstance(target, FrameworkAdapter):
        return await run_adapter_turn(target, inp, context=context)
    if isinstance(target, TurnRunner):
        return await target.run(inp, context=context)
    raise TypeError(
        f"execute_turn expected FrameworkAdapter or TurnRunner, got {type(target)!r}"
    )


async def run_oneshot_turn(
    target: TurnTarget,
    inp: AgentInput,
    *,
    events: EventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> RunResult:
    """Build the turn's context and hand ``inp`` to the adapter (or runner)."""
    tools: AgentToolsProtocol = inp.tools
    context = SimpleRunContext(
        tools=tools,
        cancellation=cancellation or NeverCancelled(),
    )
    if events is not None:
        context.events = events
    return await execute_turn(target, inp, context=context)
