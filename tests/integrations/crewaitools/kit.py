"""External CrewAI contracts and tool-boundary helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from band.core.types import Capability
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import (
    TurnEffect,
    iter_tool_definitions,
)


class MockBaseTool:
    """Minimal stand-in for crewai.tools.BaseTool at import time."""

    name: str = ""
    description: str = ""

    def __init__(self) -> None:
        pass


def _registry_names(capabilities: frozenset[Capability]) -> set[str]:
    """The platform tools the registry offers for ``capabilities``."""
    return {
        definition.name
        for definition in iter_tool_definitions(capabilities=capabilities)
    }


def _unique_names(tools: Iterable[Any]) -> set[str]:
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    return set(names)


class RecordInput(BaseModel):
    """Record a value."""

    value: str


async def record_value(args: RecordInput) -> str:
    return args.value


@declares_turn_effect(TurnEffect.ACT)
async def record_value_for_real(args: RecordInput) -> str:
    return args.value
