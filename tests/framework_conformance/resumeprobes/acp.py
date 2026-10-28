"""Resume-record probes for the ACP client adapters; see ``probe``.

The in-process ACP agent either answers the seeding prompt with a Band reply
over the adapter's injected loopback MCP server or fails the prompt with an
internal error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.runtime.tools import CHAT_ID_FIELD_NAME
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.outcomeprobes import REPLY
from tests.framework_conformance.resumeprobes.probe import SeedOutcome
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

if TYPE_CHECKING:
    from band.integrations.acp.client_adapter import ACPClientAdapter
    from tests.integrations.acp.acp_toolkit import FakeACPAgent


def seed_failure() -> type[BaseException]:
    """The error a failed seeding prompt raises out of the turn."""
    from acp.exceptions import (  # noqa: PLC0415 -- the acp (agent-client-protocol) extra is not installed in every lane's venv
        RequestError,
    )

    return RequestError


async def _fail_prompt(agent: FakeACPAgent, session_id: str) -> None:
    from acp.exceptions import (  # noqa: PLC0415 -- the acp (agent-client-protocol) extra is not installed in every lane's venv
        RequestError,
    )

    del agent, session_id
    raise RequestError.internal_error()


def _seeding_agent(outcome: SeedOutcome) -> FakeACPAgent:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        FakeACPAgent,
    )

    agent = FakeACPAgent()
    match outcome:
        case SeedOutcome.CONFIRMED:
            agent.will_call_mcp_tool(
                "tc-reply",
                REPLY.name,
                arguments={CHAT_ID_FIELD_NAME: ROOM_ID, **REPLY.arguments},
            )
        case SeedOutcome.FAILED:
            agent.on_prompt(_fail_prompt)
    return agent


async def _seed(
    adapter: ACPClientAdapter, outcome: SeedOutcome, tools: FakeAgentTools
) -> str:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    agent = _seeding_agent(outcome)
    async with started_acp_adapter(adapter, agent):
        await adapter.on_event(turn_input(tools))
    [opened] = agent.sessions
    return opened["session_id"]


async def seed_acp(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- client_adapter imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        ACPClientAdapter,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        fake_agent_config,
    )

    adapter = ACPClientAdapter(fake_agent_config(inject_band_tools=True), emit=())
    return await _seed(adapter, outcome, tools)


async def seed_omp_acp(outcome: SeedOutcome, tools: FakeAgentTools) -> str:
    from band.adapters.omp_acp import (  # noqa: PLC0415 -- omp_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        OmpACPAdapter,
    )

    return await _seed(OmpACPAdapter(emit=()), outcome, tools)
