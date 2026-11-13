"""Band custom tools served by a real Parlant tool server, as the engine calls them."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from band.integrations.parlant.sessiontools import (
    NO_SESSION_TOOLS_ERROR,
)
from band.runtime.tools import tool_result_text
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, reported_failures
from tests.integrations.parlant.custommodels import (
    MIX_FAILURE,
    OUT_OF_STOCK,
    PaintInput,
)
from tests.integrations.parlant.enginecontext import (
    activate_engine,
    bind_engine_tools,
)
from tests.integrations.parlant.helpers import SESSION_ID
from tests.integrations.parlant.samples import Shade

pytest.importorskip(
    "parlant.sdk"
)  # real p.tool and PluginServer; dev-parlant venv only


class TestCustomTools:
    async def test_advertises_the_input_models_types(self, custom_server):
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
    async def test_engine_strings_reach_the_handler_typed(
        self, custom_server, received
    ):
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
                "size": "3",
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
                size=3,
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
    async def test_invalid_value_is_a_model_visible_error(
        self, custom_server, received, invalid
    ):
        result = await custom_server.call(
            "paint", session_id=SESSION_ID, arguments={"wall": "north", **invalid}
        )

        assert result.startswith("Invalid arguments for paint:")
        assert received == []

    async def test_effect_settles_only_the_calling_rooms_turn(self, custom_server):
        calling, other = FakeAgentTools(), FakeAgentTools()
        engine = await bind_engine_tools("session-calling", calling)
        await bind_engine_tools("session-other", other)
        activate_engine(engine)

        result = await custom_server.call(
            "answer", session_id="session-calling", arguments={"text": "yes"}
        )

        assert result == "answered yes"
        assert (calling.turn.replied, other.turn.replied) == (True, False)

    async def test_unbound_session_refuses_without_running_the_handler(
        self, custom_server, received
    ):
        result = await custom_server.call(
            "paint", session_id="session-unbound", arguments={"wall": "north"}
        )

        assert result == NO_SESSION_TOOLS_ERROR
        assert received == []

    @pytest.mark.usefixtures("bound_room")
    async def test_required_nullable_field_takes_an_explicit_null(self, custom_server):
        result = await custom_server.call(
            "note", session_id=SESSION_ID, arguments={"text": None}
        )

        assert result == '{"note": null}'

    @pytest.mark.usefixtures("bound_room")
    async def test_result_with_non_text_keys_reads_as_json(self, custom_server):
        """A handler that already did its work must not read as failed because
        its result's keys are not strings."""
        result = await custom_server.call("tally", session_id=SESSION_ID, arguments={})

        assert result == '{"2026-05-04": 2}'

    async def test_raising_handler_reads_as_its_execution_failure(self, custom_server):
        """Parlant's ToolResult has no error flag, so the failure text is the
        model's only signal; the turn goes on rather than failing."""
        tools = FakeAgentTools()
        activate_engine(await bind_engine_tools(SESSION_ID, tools))

        result = await custom_server.call(
            "mix", session_id=SESSION_ID, arguments={"color": "teal"}
        )

        assert result == tool_execution_failure("mix", ValueError(MIX_FAILURE))
        assert reported_failures(tools) == []

    @pytest.mark.usefixtures("bound_room")
    async def test_returned_failure_value_passes_through_as_its_text(
        self, custom_server
    ):
        result = await custom_server.call("stock", session_id=SESSION_ID, arguments={})

        assert result == tool_result_text(OUT_OF_STOCK)
