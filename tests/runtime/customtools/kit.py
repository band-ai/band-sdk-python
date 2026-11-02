"""Shared tool definitions for these behavioral tests."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


async def failing_tool(args: WeatherInput) -> str:
    """Tool that raises an exception."""
    raise ValueError("API unavailable")
