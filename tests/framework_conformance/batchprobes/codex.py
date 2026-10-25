"""Batch-input probe for the Codex adapter; see ``batchprobes``.

The input is every text item the adapter sends the app-server in ``turn/start``.
Room history reaches Codex only when a stored thread cannot be resumed, so the
probe delivers the turn that way: the room maps a thread the app-server refuses.
"""

from __future__ import annotations

from dataclasses import replace

from band.adapters.codex import CodexAdapter
from band.core.types import AgentInput, HistoryProvider
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    turn_completed,
)
from tests.framework_conformance.batchprobes import BatchProbe, deliver

STALE_THREAD = {"message_type": "task", "metadata": {"codex_thread_id": "thr-stale"}}


def with_unresumable_thread(inp: AgentInput) -> AgentInput:
    return replace(inp, history=HistoryProvider(raw=[*inp.history.raw, STALE_THREAD]))


async def run_codex(inp: AgentInput, final: str) -> str:
    client = FakeCodexClient(
        events=[final_text(final), turn_completed()],
        resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
    )
    adapter: CodexAdapter = make_codex_adapter(client)
    await adapter.on_started("Agent", "A coding agent")
    await deliver(adapter, with_unresumable_thread(inp))
    return "\n".join(
        item["text"]
        for params in client.params_of(CodexRequestMethod.TURN_START)
        for item in params["input"]
    )


PROBES: dict[str, BatchProbe] = {"codex": BatchProbe(run=run_codex)}
