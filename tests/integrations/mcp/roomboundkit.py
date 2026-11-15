"""Kit for the room-bound Band MCP server tests: a custom tool that acts on
its room's turn."""

from __future__ import annotations

from pydantic import BaseModel

from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect


class LookupInput(BaseModel):
    """Look something up."""

    query: str


@declares_turn_effect(TurnEffect.ACT)
async def lookup(input_data: LookupInput) -> str:
    return input_data.query
