"""Resume-record probe for the Claude SDK adapter; see ``probe``.

The scripted Claude CLI (``tests/adapters/claude_sdk/fakecli.py``) either
answers the seeding prompt with a Band reply over the adapter's real MCP
server or ends it with an error result.
"""

from __future__ import annotations

from band.core.protocols import TurnResultAlreadyReported
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.outcomeprobes import REPLY
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import TurnScript, turn_input
from tests.framework_conformance.turnprobes.claudesdk import scripted_turn


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    return TurnResultAlreadyReported


async def seed_claude_sdk(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    from band.adapters.claude_sdk import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        ClaudeSDKAdapter,
    )
    from tests.adapters.claude_sdk.fakecli import (  # noqa: PLC0415 -- claude_sdk extra, absent from the standard dev-crewai/dev-parlant lane venvs
        EndTurn,
        scripted_claude,
    )

    with scripted_claude() as claude:
        match outcome:
            case SeedOutcome.CONFIRMED:
                claude.script(scripted_turn(TurnScript((REPLY,))))
            case SeedOutcome.FAILED:
                claude.script([EndTurn(is_error=True, result="seed failed")])
        adapter = ClaudeSDKAdapter(emit=())
        await adapter.on_started("Agent", "An agent under test")
        try:
            await adapter.on_event(turn_input(tools))
        finally:
            await adapter.cleanup_all()
    [opened] = claude.sessions
    return opened.session_id
