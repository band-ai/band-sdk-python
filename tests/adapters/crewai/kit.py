from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel, Field

from band.adapters.crewai import EMPTY_LLM_RESPONSE_MARKER
from band.runtime.tools import BandTool

if TYPE_CHECKING:
    from band.adapters.crewai import CrewAIAdapter as CrewAIAdapterType


EMPTY_LLM_RESPONSE_ERROR = f"{EMPTY_LLM_RESPONSE_MARKER} - None or empty."

EMPTY_COMPLETION = ValueError(EMPTY_LLM_RESPONSE_ERROR)

ToolStep = tuple[str, dict[str, Any]]

ANSWER = "The vault code is 4471-ECHO."

REPLY: ToolStep = (BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": ["@alice"]})

FAILED_REPLY: ToolStep = (BandTool.SEND_MESSAGE, {"content": ANSWER, "mentions": []})

DECLINE: ToolStep = (BandTool.NO_REPLY, {"reason": "FYI only"})

ACT: ToolStep = (BandTool.CREATE_CHATROOM, {})

LOOKUP: ToolStep = (BandTool.LOOKUP_PEERS, {})

NARRATION: ToolStep = (
    BandTool.SEND_EVENT,
    {"content": "thinking", "message_type": "thought"},
)


async def started(adapter_cls: type[CrewAIAdapterType]) -> CrewAIAdapterType:
    adapter = adapter_cls()
    await adapter.on_started("TestBot", "Test bot")
    return adapter


async def call_crew_tool(adapter: CrewAIAdapterType, step: ToolStep) -> None:
    """Run one of the crew's real tools as CrewAI does: synchronously, off the
    event loop thread, so the bridge posts back on the adapter's loop."""
    name, arguments = step
    tool = next(t for t in adapter.create_crewai_tools() if t.name == name)
    await asyncio.to_thread(tool._run, **arguments)


def scripted_crew(
    adapter: CrewAIAdapterType,
    *steps: ToolStep,
    ending: str | Exception | None,
) -> MagicMock:
    """A crew agent whose kickoff runs ``steps`` through the real tools, then
    returns ``ending`` as the final text (``None`` for a falsey result) or
    raises it."""

    async def kickoff(_prompt: str) -> Any:
        for step in steps:
            await call_crew_tool(adapter, step)
        if isinstance(ending, Exception):
            raise ending
        return None if ending is None else MagicMock(raw=ending)

    return MagicMock(kickoff_async=AsyncMock(side_effect=kickoff))


class MockBaseTool:
    name: str = ""
    description: str = ""

    def __init__(self):
        pass


class EchoInput(BaseModel):
    """Echo back the provided message."""

    message: str = Field(description="Message to echo")


class CalculatorInput(BaseModel):
    """Perform math calculations."""

    operation: str = Field(description="add, subtract, multiply, divide")
    left: float = Field(description="Left operand")
    right: float = Field(description="Right operand")


async def echo_message(args: EchoInput) -> str:
    """Async echo tool."""
    return f"Echo: {args.message}"


def calculate(args: CalculatorInput) -> str:
    """Sync calculator tool."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    return str(ops[args.operation](args.left, args.right))


async def failing_tool(args: EchoInput) -> str:
    """Tool that always fails."""
    raise ValueError("Service unavailable")
