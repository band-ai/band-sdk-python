"""Portable custom tools and a tool-calling graph for the custom-tool regressions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field


class EchoInput(BaseModel):
    """Echo the given text."""

    text: str = Field(description="text to echo")


def echo(args: EchoInput) -> str:
    return f"echo:{args.text}"


class FileInput(BaseModel):
    """File the report."""

    note: str


class CallsToolGraph:
    """A graph whose one step calls the named tool, as a real agent graph would."""

    def __init__(self, turn_tools: list[Any], tool_name: str) -> None:
        (self._tool,) = [t for t in turn_tools if t.name == tool_name]

    async def astream_events(
        self, graph_input: dict[str, Any], **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        await self._tool.ainvoke({"note": "go"})
        return
        yield
