"""Band custom tools served by a real Parlant tool server, as the engine calls them."""

from __future__ import annotations

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
from tests.integrations.parlant.helpers import SESSION_ID
from tests.integrations.parlant.samples import MaybeTrays, Shade

pytest.importorskip(
    "parlant.sdk"
)  # real p.tool and PluginServer; dev-parlant venv only


@pytest.fixture
def bound_room() -> None:
    """Bind ``SESSION_ID`` to a room, so calls on it resolve their tools."""
    set_session_tools(SESSION_ID, FakeAgentTools())


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
    size: int | str = 1
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
                (TallyInput, tally),
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
    # The shape table's rows are pinned in test_customschema; this pins that
    # the descriptors reach the engine in place of p.tool's own.
    assert {
        name: descriptors[name] for name in ("wall", "coats", "shade", "colors")
    } == {
        "wall": {"type": "string", "description": "Which wall to paint"},
        "coats": {"type": "integer"},
        "shade": {"type": "string", "enum": ["light", "dark"]},
        "colors": {"type": "array", "item_type": "string", "enum": ["red", "blue"]},
    }


@pytest.mark.usefixtures("bound_room")
async def test_engine_strings_reach_the_handler_typed(custom_server, received):
    result = await custom_server.call(
        "paint",
        session_id=SESSION_ID,
        arguments={
            "wall": "12",
            "coats": "3",
            "primer": "False",
            "finish": "gloss",
            "shade": "dark",
            "due": "2026-05-04",
            "rooms": "[1, 2]",
            "dried": "[true, false]",
            "trays": "[3, 4]",
            "budget": None,
            "cost": "3.50",
            "start": "2026-05-04T10:00:00Z",
            "colors": "['red', 'blue']",
            "size": "large",
            "layers": "3",
            "sheen": "satin",
            "trim": None,
        },
    )

    assert result == '{"painted": "12"}'
    assert received == [
        PaintInput(
            wall="12",
            coats=3,
            primer=False,
            finish="gloss",
            shade=Shade.DARK,
            due=date(2026, 5, 4),
            rooms=[1, 2],
            dried=[True, False],
            trays=[3, 4],
            budget=100,
            cost=Decimal("3.50"),
            start=datetime(2026, 5, 4, 10, tzinfo=UTC),
            colors=["red", "blue"],
            size="large",
            layers=3,
            sheen="satin",
        )
    ]


@pytest.mark.usefixtures("bound_room")
@pytest.mark.parametrize(
    "invalid",
    [{"coats": "three"}, {"rooms": "[1, 2"}, {"rooms": "{[1]: 2}"}],
    ids=["bad-scalar", "malformed-list", "unhashable-list-text"],
)
async def test_invalid_value_is_a_model_visible_error(custom_server, received, invalid):
    result = await custom_server.call(
        "paint", session_id=SESSION_ID, arguments={"wall": "north", **invalid}
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


@pytest.mark.usefixtures("bound_room")
async def test_required_nullable_field_takes_an_explicit_null(custom_server):
    result = await custom_server.call(
        "note", session_id=SESSION_ID, arguments={"text": None}
    )

    assert result == '{"note": null}'


@pytest.mark.usefixtures("bound_room")
async def test_result_with_non_text_keys_reads_as_json(custom_server):
    """A handler that already did its work must not read as failed because
    its result's keys are not strings."""
    result = await custom_server.call("tally", session_id=SESSION_ID, arguments={})

    assert result == '{"2026-05-04": 2}'
