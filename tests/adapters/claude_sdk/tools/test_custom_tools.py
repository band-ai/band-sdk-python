from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel, Field

from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Capability
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.adapters.claude_sdk.helpers import (
    MISSING_REPLY_TEXT,
    ClaudeRoom,
    with_approvals,
)

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class EchoInput(BaseModel):
    """Echo the message."""

    message: str = Field(description="Message to echo")


class CalculatorInput(BaseModel):
    """Add two numbers."""

    a: float
    b: float


async def echo(args: EchoInput) -> str:
    return f"Echo: {args.message}"


def add(args: CalculatorInput) -> float:
    return args.a + args.b


class FileTicketInput(BaseModel):
    """File a ticket."""

    title: str


@declares_turn_effect(TurnEffect.ACT)
async def file_ticket(args: FileTicketInput) -> str:
    return f"Filed: {args.title}"


CUSTOM_TOOLS = [(EchoInput, echo), (CalculatorInput, add)]


async def test_custom_tools_run_through_the_band_server_without_approval(
    claude_room: OpenRoom,
) -> None:
    """Custom tools are named from their input model, served beside the Band
    tools, and pre-approved: even under manual approval nothing waits on the
    room."""
    room = await claude_room(with_approvals(), additional_tools=CUSTOM_TOOLS)
    room.claude.script(
        [
            room.model_call("echo", message="ping"),
            room.model_call("calculator", a=2, b=3),
            room.model_reply("pong, and 5"),
        ]
    )

    await room.send("echo ping and add 2+3")

    assert room.chat == ["pong, and 5"]
    assert "Echo: ping" in str(room.tool_outputs["echo"])
    assert "5" in str(room.tool_outputs["calculator"])


@pytest.mark.parametrize(
    ("capabilities", "served"),
    [(Capability.MEMORY, True), ((), False)],
    ids=["memory-enabled", "memory-disabled"],
)
async def test_memory_tools_are_served_only_with_the_memory_capability(
    claude_room: OpenRoom, capabilities: Capability | tuple[()], served: bool
) -> None:
    room = await claude_room(additional_tools=CUSTOM_TOOLS, capabilities=capabilities)
    room.claude.script([room.model_call("band_list_memories"), room.model_reply("ok")])

    await room.send("what do you remember?")

    output = str(room.tool_outputs["band_list_memories"])
    assert ("No such tool available" not in output) is served
    assert room.chat == ["ok"]


async def test_a_custom_tool_records_its_declared_effect_on_the_rooms_turn(
    claude_room: OpenRoom,
) -> None:
    """A custom tool declared as real work completes the turn without a
    reply; an undeclared one only observes, so its turn is reported missing."""
    room = await claude_room(
        additional_tools=[*CUSTOM_TOOLS, (FileTicketInput, file_ticket)]
    )
    room.claude.script(
        [room.model_call("fileticket", title="Broken build")],
        [room.model_call("echo", message="ping")],
    )
    ticket, echo_only = room.fresh_tools(), room.fresh_tools()

    await room.send("file a ticket for the broken build", tools=ticket)
    with pytest.raises(TurnResultAlreadyReported):
        await room.send("echo ping", tools=echo_only)

    assert (ticket.turn.complete, ticket.turn.replied) == (True, False)
    assert room.failures == [MISSING_REPLY_TEXT]
