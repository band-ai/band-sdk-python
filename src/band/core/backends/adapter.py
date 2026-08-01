"""Shim wrapping ``SimpleAdapter`` / ``FrameworkAdapter`` as ``AgentBackend``."""

from __future__ import annotations

from typing import Any, cast

from dataclasses import replace

from band.core.contracts import (
    BackendContext,
    RunResult,
)
from band.core.backends.observing import ObservingTools
from band.core.protocols import AgentToolsProtocol, FrameworkAdapter, RunContext
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput


class SimpleAdapterBackend:
    """Wrap a ``SimpleAdapter`` / ``FrameworkAdapter`` as an ``AgentBackend``."""

    def __init__(self, adapter: FrameworkAdapter | SimpleAdapter[Any]) -> None:
        self._adapter = adapter

    async def start(self, context: BackendContext) -> None:
        await self._adapter.on_started(context.agent_name, context.agent_description)

    async def run(self, inp: AgentInput, *, context: RunContext) -> RunResult:
        # ``on_message`` takes neither the turn's sink nor its token, so the
        # per-turn proxy carries the whole context: tool-first adapters (ACP
        # RoomTurnEmitter) dual-write TurnEvents onto the same sink
        # AgentStream.observe reads, and façade adapters running their own inner
        # backend cancel with the outer turn. Both reach it via turn_context().
        observing = ObservingTools(_inner=context.tools, turn=context)
        # __getattr__ forwarders are invisible to static analysis.
        inp = replace(inp, tools=cast(AgentToolsProtocol, observing))
        await self._adapter.on_event(inp)
        return RunResult(
            usage=None,
            delivery=observing.receipt,
        )

    async def close_session(self, session_id: str) -> None:
        await self._adapter.on_cleanup(session_id)

    async def aclose(self) -> None:
        cleanup_all = getattr(self._adapter, "cleanup_all", None)
        if cleanup_all is not None:
            await cleanup_all()
