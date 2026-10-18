"""Backend-stop probes for the cursor_acp adapter; see ``stopprobes``.

Cursor runs its turn detached so a permission decision can release
``on_message``; a STOP must still reach that turn's prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    stop_room,
    wait_until,
)
from tests.framework_conformance.stopprobes.acp import (
    CANCEL_FIRST_SESSION,
    cancels,
    end_prompting_turn,
    prompting_agent,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

if TYPE_CHECKING:
    from band.adapters.cursor_acp import CursorACPAdapter

# How the agent's permission request ended once the STOP cancelled it.
DECISION_CANCELLED = "decision cancelled"


def cursor_adapter() -> CursorACPAdapter:
    from band.adapters.cursor_acp import (  # noqa: PLC0415 -- cursor_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CursorACPAdapter,
    )

    return CursorACPAdapter()


async def stop_parked_on_decision() -> list[str]:
    """A STOP after ``on_message`` returned on an open decision cancels the
    decision and the agent's prompt."""
    from acp.schema import (  # noqa: PLC0415 -- the acp (agent-client-protocol) extra; not installed in every lane's venv
        DeniedOutcome,
    )

    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        FakeACPAgent,
        started_acp_adapter,
    )

    adapter = cursor_adapter()
    agent = FakeACPAgent().will_ask_permission()
    async with started_acp_adapter(adapter, agent):
        await adapter.on_event(turn_input(FakeAgentTools()))
        await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await wait_until(lambda: bool(agent.permission_responses))
        await adapter.backend_turns.drain()
        decisions = [
            DECISION_CANCELLED
            for response in agent.permission_responses
            if isinstance(response.outcome, DeniedOutcome)
        ]
        return [*decisions, *cancels(agent)]


async def idle() -> list[str]:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = cursor_adapter()
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        await stop_room(adapter, ROOM_ID)
        return cancels(agent)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-while-prompting": StopCase(
            run=lambda mode=mode: end_prompting_turn(cursor_adapter(), mode),
            expected=[CANCEL_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "stop-parked-on-decision": StopCase(
        run=stop_parked_on_decision,
        expected=[DECISION_CANCELLED, CANCEL_FIRST_SESSION],
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
