"""Turn-outcome probes for the ACP client adapters; see ``turnprobes``.

The scripted ACP agent calls each Band tool over the adapter's injected
loopback MCP server, so every call runs through the real tool dispatch into
the turn's tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.runtime.tools import CHAT_ID_FIELD_NAME
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    ROOM_ID,
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
    user_message,
)

if TYPE_CHECKING:
    from band.core.types import AgentInput
    from band.integrations.acp.client_adapter import ACPClientAdapter
    from tests.integrations.acp.acp_toolkit import FakeACPAgent


def _scripted_agent(script: TurnScript) -> FakeACPAgent:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        FakeACPAgent,
    )

    agent = FakeACPAgent()
    for index, call in enumerate(script.tool_calls):
        agent.will_call_mcp_tool(
            f"tc-{index}",
            call.name,
            arguments={CHAT_ID_FIELD_NAME: ROOM_ID, **call.arguments},
        )
    if script.final_text:
        agent.will_say(script.final_text)
    return agent


async def _drive(
    adapter: ACPClientAdapter, agent: FakeACPAgent, inp: AgentInput
) -> None:
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        started_acp_adapter,
    )

    async with started_acp_adapter(adapter, agent):
        await adapter.on_event(inp)


async def run_acp(script: TurnScript, tools: FakeAgentTools) -> None:
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- client_adapter imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        ACPClientAdapter,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- the ACP toolkit imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        fake_agent_config,
    )

    adapter = ACPClientAdapter(fake_agent_config(inject_band_tools=True))
    await _drive(adapter, _scripted_agent(script), turn_input(tools))


def _cursor_adapter() -> ACPClientAdapter:
    from band.adapters.cursor_acp import (  # noqa: PLC0415 -- cursor_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CursorACPAdapter,
    )

    return CursorACPAdapter()


async def run_cursor_acp(script: TurnScript, tools: FakeAgentTools) -> None:
    await _drive(_cursor_adapter(), _scripted_agent(script), turn_input(tools))


async def settle_cursor_acp(tools: FakeAgentTools) -> None:
    """``/cursor decisions`` is answered by the adapter, never the agent."""
    await _drive(
        _cursor_adapter(),
        _scripted_agent(TurnScript()),
        turn_input(tools, user_message("/cursor decisions")),
    )


PROBES: dict[str, TurnOutcomeProbe] = {
    "acp": TurnOutcomeProbe(run=run_acp),
    "cursor_acp": TurnOutcomeProbe(run=run_cursor_acp, settle=settle_cursor_acp),
}
