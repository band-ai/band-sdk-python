"""Batch-input probe for the Codex adapter; see ``batchprobes``.

The input is every text item the adapter sends the app-server in ``turn/start``.
Room history reaches Codex in the seeding turn of a fresh thread, which a room
with no recorded thread starts.
"""

from __future__ import annotations

from band.adapters.codex import CodexAdapter
from band.core.types import AgentInput
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    turn_completed,
)
from tests.framework_conformance.batchprobes import BatchProbe, deliver


async def run_codex(inp: AgentInput, final: str) -> str:
    client = FakeCodexClient(events=[final_text(final), turn_completed()])
    adapter: CodexAdapter = make_codex_adapter(client)
    await adapter.on_started("Agent", "A coding agent")
    await deliver(adapter, inp)
    return "\n".join(
        item["text"]
        for params in client.params_of(CodexRequestMethod.TURN_START)
        for item in params["input"]
    )


PROBES: dict[str, BatchProbe] = {"codex": BatchProbe(run=run_codex)}
