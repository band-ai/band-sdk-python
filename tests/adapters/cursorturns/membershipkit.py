"""Native permission work and judged batches for Cursor membership tests."""

from __future__ import annotations

from dataclasses import dataclass, replace

from acp.schema import AllowedOutcome, ToolCallUpdate

from band.core.protocols import AgentToolsProtocol
from band.core.turn import Turn
from band.core.types import AgentInput
from tests.adapters.cursorturns.kit import ROOM_A
from tests.framework_conformance.invocationkit import turn_input
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent, Gate, allow_or_reject
from tests.integrations.acp.acp_toolkit.harness import TranscriptTools, live_message

ALICE = "alice"
BOB = "bob"


@dataclass
class PermissionWork:
    """Keep provider completion separate from its native permission response."""

    agent: FakeACPAgent
    finishing: Gate


def permission_work(*, requests: int = 1) -> PermissionWork:
    agent = FakeACPAgent()
    finishing = Gate()

    @agent.on_prompt
    async def run(peer: FakeACPAgent, session_id: str) -> None:
        for index in range(requests):
            response = await peer.ask_permission(
                session_id,
                ToolCallUpdate(tool_call_id=f"tool-{index}", title="shell"),
                allow_or_reject(),
            )
            peer.approved = isinstance(response.outcome, AllowedOutcome)
        await finishing.hold()

    return PermissionWork(agent, finishing)


def audience_input(tools: AgentToolsProtocol, *senders: str) -> AgentInput:
    """Deliver the original sender sequence through the judged batch entry."""
    return replace(
        turn_input(
            *(
                live_message(f"input-{index}", room_id=ROOM_A, sender_id=sender)
                for index, sender in enumerate(senders)
            ),
            room_id=ROOM_A,
        ),
        tools=tools,
    )


async def departed_tools(*senders: str) -> TranscriptTools:
    """Finish membership setup before admitting the new turn's ledger."""
    tools = TranscriptTools()
    for sender in senders:
        await tools.remove_participant(sender)
    tools.turn = Turn()
    return tools
