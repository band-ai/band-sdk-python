"""Resume-record probe for the Copilot SDK adapter through its fake client.

The fake client either answers the seeding prompt or raises from it, as the
real SDK's ``send_and_wait`` does on a session error.
"""

from __future__ import annotations

from band.testing.fake_tools import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import FakeCopilotClient, make_started_adapter
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import turn_input


class SeedPromptFailed(RuntimeError):
    """The scripted session error a failed seeding prompt raises."""


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    return SeedPromptFailed


async def seed_copilot_sdk(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    errors = [SeedPromptFailed()] if outcome == SeedOutcome.FAILED else []
    client = FakeCopilotClient(prompt_errors=errors)
    adapter = await make_started_adapter(client, emit=())
    await adapter.on_event(turn_input(tools))
    [opened] = client.sessions
    return opened.session_id
