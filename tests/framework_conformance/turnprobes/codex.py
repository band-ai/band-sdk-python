"""Turn-outcome probes for the codex adapter(s); see ``turnprobes``."""

from __future__ import annotations

from band.adapters.codex import CodexAdapter, CodexCommand
from band.integrations.codex import RpcEvent
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    user_message,
)


def scripted_events(script: TurnScript) -> list[RpcEvent]:
    """The app-server events of one Codex turn following ``script``."""
    events = [
        tool_call_request(request_id, call.name, call.arguments)
        for request_id, call in enumerate(script.tool_calls, start=1)
    ]
    if script.final_text:
        events.append(final_text(script.final_text))
    return [*events, turn_completed()]


async def started_adapter(*events: RpcEvent) -> CodexAdapter:
    adapter = make_codex_adapter(FakeCodexClient(events=list(events)))
    await adapter.on_started("Agent", "A coding agent")
    return adapter


async def run_codex(script: TurnScript, tools: FakeAgentTools) -> None:
    adapter = await started_adapter(*scripted_events(script))
    await adapter.on_event(turn_input(tools))


async def ask_codex_status(tools: FakeAgentTools) -> None:
    adapter = await started_adapter()
    await adapter.on_event(turn_input(tools, user_message(f"/{CodexCommand.STATUS}")))


PROBES: dict[str, TurnOutcomeProbe] = {
    "codex": TurnOutcomeProbe(run=run_codex, settle=ask_codex_status, relays=False),
}
