"""Run one turn through ``AgentBackend.run``.

Claim/mark/drain stay in the invoker; this is only the turn boundary:
``AgentInput`` → ``RunResult``.
"""

from __future__ import annotations

from band.core.contracts import RunResult
from band.core.protocols import (
    AgentBackend,
    AgentToolsProtocol,
    CancellationToken,
    EventSink,
)
from band.core.run.cancellation import NeverCancelled
from band.core.run.context import SimpleRunContext
from band.core.types import AgentInput


async def run_oneshot_turn(
    backend: AgentBackend,
    inp: AgentInput,
    *,
    events: EventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> RunResult:
    """Build the turn's context and hand ``inp`` to the backend."""
    tools: AgentToolsProtocol = inp.tools
    context = SimpleRunContext(
        tools=tools,
        cancellation=cancellation or NeverCancelled(),
    )
    if events is not None:
        context.events = events
    return await backend.run(inp, context=context)
