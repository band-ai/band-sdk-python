from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel, Field

from band.core.types import Capability
from tests.adapters.claude_sdk.helpers import ClaudeRoom

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


CUSTOM_TOOLS = [(EchoInput, echo), (CalculatorInput, add)]


async def test_custom_tools_run_through_the_band_server_without_approval(
    claude_room: OpenRoom,
) -> None:
    """Custom tools are named from their input model, served beside the Band
    tools, and pre-approved: even under manual approval nothing waits on the
    room."""
    room = await claude_room(additional_tools=CUSTOM_TOOLS, approval_mode="manual")
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
