"""Resume-record probe for the Codex adapter; see ``probe``.

The fake app-server either answers the seeding turn with a Band reply or
refuses ``turn/start``.
"""

from __future__ import annotations

from band.adapters.codex import CodexAdapter
from band.integrations.codex import CodexJsonRpcError
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    make_codex_adapter,
)
from tests.framework_conformance.outcomeprobes import REPLY
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import TurnScript, turn_input
from tests.framework_conformance.turnprobes.codex import scripted_events


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    return CodexJsonRpcError


def _app_server(outcome: SeedOutcome) -> FakeCodexClient:
    match outcome:
        case SeedOutcome.CONFIRMED:
            return FakeCodexClient(events=scripted_events(TurnScript((REPLY,))))
        case SeedOutcome.FAILED:
            return FakeCodexClient(
                turn_start_error=CodexJsonRpcError(code=-32603, message="refused")
            )


async def seed_codex(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    adapter: CodexAdapter = make_codex_adapter(_app_server(outcome), emit=())
    await adapter.on_started("Agent", "A coding agent")
    await adapter.on_event(turn_input(tools))
    return FIRST_THREAD_ID
