"""Backend-stop probes for the ACP client adapter; see ``stopprobes``.

The adapter is paired in process with a scripted ACP agent over real JSON-RPC,
and the agent records each ``session/cancel`` and connection exit it sees.
CopilotACPAdapter and OmpACPAdapter keep this adapter's ``on_message`` and
``backend_turns``, so these cases cover them too.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_idle_room,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

if TYPE_CHECKING:
    from band.integrations.acp.client_adapter import ACPClientAdapter
    from tests.integrations.acp.acp_toolkit import FakeACPAgent

# The scripted agent's first session, as it records the cancel and the exit.
CANCEL_FIRST_SESSION = "session/cancel fake-session-1"
CONNECTION_EXIT = "connection exit"
# Short enough that the timeout case ends at once.
PROBE_TURN_TIMEOUT_S = 0.05


def prompting_agent() -> FakeACPAgent:
    """An agent whose prompt keeps running until its connection closes."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        FakeACPAgent,
    )

    agent = FakeACPAgent()

    @agent.on_prompt
    async def keep_prompting(fake: FakeACPAgent, session_id: str) -> None:
        await asyncio.Future[None]()

    return agent


def cancels(agent: FakeACPAgent) -> list[str]:
    return [record for record in agent.lifecycle if record != CONNECTION_EXIT]


def acp_adapter(**settings: object) -> ACPClientAdapter:
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- client_adapter imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        ACPClientAdapter,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        fake_agent_config,
    )

    return ACPClientAdapter(fake_agent_config(**settings))


async def end_prompting_turn(adapter: ACPClientAdapter, mode: ControlMode) -> list[str]:
    """End a turn whose prompt is in flight; the agent's cancels."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        await wait_until(lambda: bool(agent.prompts))
        await end_turn(adapter, turn, ROOM_ID, mode)
        return cancels(agent)


async def idle() -> list[str]:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter()
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        await stop_idle_room(adapter, ROOM_ID)
        return cancels(agent)


async def timed_out() -> list[str]:
    """The cancel goes out before the timed-out room's runtime is stopped."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter(turn_timeout_s=PROBE_TURN_TIMEOUT_S)
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        with suppress(Exception):
            await adapter.on_event(turn_input(FakeAgentTools()))
        return list(agent.lifecycle)


async def cleanup_after_stop() -> list[str]:
    """Leaving the room right after a STOP stops the runtime only after the
    pending cancel is sent."""
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    adapter = acp_adapter()
    agent = prompting_agent()
    async with started_acp_adapter(adapter, agent):
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        await wait_until(lambda: bool(agent.prompts))
        turn.cancel()
        with suppress(asyncio.CancelledError):
            await turn
        await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
        await adapter.on_cleanup(ROOM_ID)
        return list(agent.lifecycle)


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_prompting_turn(acp_adapter(), mode),
            expected=[CANCEL_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "idle-room": StopCase(run=idle, expected=[]),
    "timeout": StopCase(
        run=timed_out, expected=[CANCEL_FIRST_SESSION, CONNECTION_EXIT]
    ),
    "cleanup-after-stop": StopCase(
        run=cleanup_after_stop, expected=[CANCEL_FIRST_SESSION, CONNECTION_EXIT]
    ),
}
