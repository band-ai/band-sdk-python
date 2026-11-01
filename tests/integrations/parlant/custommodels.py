"""Validated inputs and handlers for real Parlant custom tool calls."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictInt

from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from tests.integrations.parlant.samples import MaybeTrays, Shade


class PaintInput(BaseModel):
    """Paint a wall."""

    wall: str = Field(description="Which wall to paint")
    coats: int = 1
    primer: bool = True
    finish: Literal["matte", "gloss"] = "matte"
    shade: Shade = Shade.LIGHT
    due: date = date(2026, 1, 1)
    rooms: list[int] = []
    dried: list[bool] = []
    trays: MaybeTrays = None
    budget: Annotated[int, Field(description="Spend cap")] | None = 100
    cost: Decimal = Decimal(0)
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    colors: list[Literal["red", "blue"]] = []
    size: Literal["auto"] | int = "auto"
    layers: StrictInt = 1
    sheen: Literal["satin", "gloss", None] = None  # noqa: PYI061 -- the form under test
    trim: Annotated[str, Field(description="Trim style")] | None = Field(
        None, description="Trim to paint"
    )


class NoteInput(BaseModel):
    """Leave a note."""

    text: str | None


class Receipt(BaseModel):
    note: str | None


class TallyInput(BaseModel):
    """Count the coats applied per day."""


async def tally(args: TallyInput) -> dict[date, int]:
    return {date(2026, 5, 4): 2}


class AnswerInput(BaseModel):
    """Answer the room directly."""

    text: str


@declares_turn_effect(TurnEffect.REPLY)
async def answer(args: AnswerInput) -> str:
    return f"answered {args.text}"
