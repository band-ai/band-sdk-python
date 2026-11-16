"""Shared tool definitions for these behavioral tests."""

from __future__ import annotations

from pydantic import BaseModel, Field

from band.runtime.custom_tools import (
    CustomToolDef,
    declares_turn_effect,
    get_custom_tool_name,
)
from band.runtime.tools import TurnEffect


class WeatherInput(BaseModel):
    """Get current weather for a city."""

    city: str = Field(description="City name")


class CalculatorInput(BaseModel):
    """Perform math calculations."""

    operation: str = Field(description="add, subtract, multiply, divide")
    left: float
    right: float


class SearchWebInput(BaseModel):
    """Search the web for information."""

    query: str = Field(description="Search query")
    max_results: int = Field(default=10, description="Maximum results to return")


class NoDocstringInput(BaseModel):
    value: str


async def async_weather(args: WeatherInput) -> str:
    """Async weather implementation."""
    return f"Weather in {args.city}: Sunny, 72F"


def sync_calculator(args: CalculatorInput) -> str:
    """Sync calculator implementation."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    result = ops[args.operation](args.left, args.right)
    return str(result)


API_UNAVAILABLE = "API unavailable"


class FrameworkSignal(Exception):
    """An exception the calling framework handles itself."""


async def failing_tool(args: WeatherInput) -> str:
    """Tool that raises an exception."""
    raise ValueError(API_UNAVAILABLE)


class SkipInput(BaseModel):
    """Decline to reply to this message."""


SKIPPED = "skipped"


@declares_turn_effect(TurnEffect.DECLINE)
async def skip(args: SkipInput) -> str:
    """A custom tool declared as the turn's decline."""
    return SKIPPED


class DeployInput(BaseModel):
    """Deploy the service."""


SKIP = get_custom_tool_name(SkipInput)
DEPLOY = get_custom_tool_name(DeployInput)


def recording_deploy() -> tuple[CustomToolDef, list[DeployInput]]:
    """A custom tool declared as real work, and the calls it ran."""
    ran: list[DeployInput] = []

    @declares_turn_effect(TurnEffect.ACT)
    async def deploy(args: DeployInput) -> str:
        ran.append(args)
        return "deployed"

    return (DeployInput, deploy), ran
