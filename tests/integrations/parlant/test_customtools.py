"""Band custom tools served by a real Parlant tool server, as the engine calls them."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field, StrictInt

from band.integrations.parlant.sessiontools import (
    NO_SESSION_TOOLS_ERROR,
    set_session_tools,
)
from band.integrations.parlant.tools import create_parlant_tools
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import FakeAgentTools

pytest.importorskip(
    "parlant.sdk"
)  # real p.tool and PluginServer; dev-parlant venv only

SESSION = "session-1"


@pytest.fixture
def bound_room() -> None:
    """Bind ``SESSION`` to a room, so calls on it resolve their tools."""
    set_session_tools(SESSION, FakeAgentTools())


class Shade(enum.Enum):
    LIGHT = "light"
    DARK = "dark"


class Grade(enum.IntEnum):
    ECONOMY = 1
    PREMIUM = 2


class PaintInput(BaseModel):
    """Paint a wall."""

    wall: str = Field(description="Which wall to paint")
    coats: int = 1
    primer: bool = True
    finish: Literal["matte", "gloss"] = "matte"
    shade: Shade = Shade.LIGHT
    due: date = date(2026, 1, 1)
    rooms: list[int] = []
    budget: Annotated[int, Field(description="Spend cap")] | None = 100
    grade: Grade = Grade.ECONOMY
    cost: Decimal = Decimal(0)
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    colors: list[Literal["red", "blue"]] = []
    size: int | str = 1
    layers: StrictInt = 1
    sheen: Literal["satin", "gloss", None] = None  # noqa: PYI061 -- the form under test
    trim: Annotated[str, Field(description="Trim style")] | None = Field(
        None, description="Trim to paint"
    )


class NoteInput(BaseModel):
    """Leave a note."""

    text: str | None
    tone: Literal["calm"] = "calm"


class Receipt(BaseModel):
    note: str | None


class AnswerInput(BaseModel):
    """Answer the room directly."""

    text: str


@declares_turn_effect(TurnEffect.REPLY)
async def answer(args: AnswerInput) -> str:
    return f"answered {args.text}"


@pytest.fixture
def received() -> list[PaintInput]:
    """Every validated input the paint handler ran with."""
    return []


@pytest_asyncio.fixture(loop_scope="function")
async def custom_server(plugin_server, received):
    async def paint(args: PaintInput) -> dict[str, object]:
        received.append(args)
        return {"painted": args.wall}

    async def note(args: NoteInput) -> Receipt:
        return Receipt(note=args.text)

    await plugin_server.enable(
        create_parlant_tools(
            custom_tools=[
                (PaintInput, paint),
                (AnswerInput, answer),
                (NoteInput, note),
            ]
        )
    )
    return plugin_server


async def test_advertises_the_input_models_types(custom_server):
    tool = await custom_server.advertised("paint")

    descriptors = {
        name: descriptor for name, (descriptor, _) in tool["parameters"].items()
    }
    assert tool["description"] == "Paint a wall."
    assert tool["required"] == ["wall"]
    assert descriptors == {
        "wall": {"type": "string", "description": "Which wall to paint"},
        "coats": {"type": "integer"},
        "primer": {"type": "boolean"},
        "finish": {"type": "string", "enum": ["matte", "gloss"]},
        "shade": {"type": "string", "enum": ["light", "dark"]},
        "due": {"type": "date"},
        "rooms": {"type": "array", "item_type": "integer"},
        "budget": {"type": "integer", "description": "Spend cap"},
        "grade": {"type": "string", "enum": ["1", "2"]},
        "cost": {"type": "string"},
        "start": {"type": "datetime"},
        "colors": {"type": "array", "item_type": "string", "enum": ["red", "blue"]},
        "size": {"type": "string"},
        "layers": {"type": "integer"},
        "sheen": {"type": "string", "enum": ["satin", "gloss"]},
        "trim": {"type": "string", "description": "Trim to paint"},
    }


@pytest.mark.usefixtures("bound_room")
async def test_engine_strings_reach_the_handler_typed(custom_server, received):
    result = await custom_server.call(
        "paint",
        session_id=SESSION,
        arguments={
            "wall": "north",
            "coats": "3",
            "primer": "False",
            "finish": "gloss",
            "shade": "dark",
            "due": "2026-05-04",
            "rooms": "[1, 2]",
            "budget": None,
            "grade": "2",
            "cost": "3.50",
            "start": "2026-05-04T10:00:00Z",
            "colors": "['red', 'blue']",
            "size": "large",
            "layers": "3",
            "sheen": "satin",
            "trim": None,
        },
    )

    assert result == '{"painted": "north"}'
    assert received == [
        PaintInput(
            wall="north",
            coats=3,
            primer=False,
            finish="gloss",
            shade=Shade.DARK,
            due=date(2026, 5, 4),
            rooms=[1, 2],
            budget=100,
            grade=Grade.PREMIUM,
            cost=Decimal("3.50"),
            start=datetime(2026, 5, 4, 10, tzinfo=UTC),
            colors=["red", "blue"],
            size="large",
            layers=3,
            sheen="satin",
        )
    ]


@pytest.mark.usefixtures("bound_room")
async def test_invalid_value_is_a_model_visible_error(custom_server, received):
    result = await custom_server.call(
        "paint", session_id=SESSION, arguments={"wall": "north", "coats": "three"}
    )

    assert result.startswith("Error running paint: Invalid arguments for paint:")
    assert received == []


async def test_effect_settles_only_the_calling_rooms_turn(custom_server):
    calling, other = FakeAgentTools(), FakeAgentTools()
    set_session_tools("session-calling", calling)
    set_session_tools("session-other", other)

    result = await custom_server.call(
        "answer", session_id="session-calling", arguments={"text": "yes"}
    )

    assert result == "answered yes"
    assert (calling.turn.replied, other.turn.replied) == (True, False)


async def test_unbound_session_refuses_without_running_the_handler(
    custom_server, received
):
    result = await custom_server.call(
        "paint", session_id="session-unbound", arguments={"wall": "north"}
    )

    assert result == NO_SESSION_TOOLS_ERROR
    assert received == []


async def test_single_value_literal_is_advertised_as_its_enum(custom_server):
    tool = await custom_server.advertised("note")

    assert tool["parameters"]["tone"][0] == {"type": "string", "enum": ["calm"]}


@pytest.mark.usefixtures("bound_room")
async def test_required_nullable_field_takes_an_explicit_null(custom_server):
    result = await custom_server.call(
        "note", session_id=SESSION, arguments={"text": None}
    )

    assert result == '{"note": null}'
