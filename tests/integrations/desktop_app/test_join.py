"""Resolve room names and report join failures."""

from __future__ import annotations

from typing import Any

import pytest

from band.integrations.desktop_app.tools import RoomTool
from tests.integrations.desktop_app.conftest import message


class TestFindingTheRoom:
    """`band_join_room` takes whatever the user said, not just an id."""

    async def test_a_room_name_resolves_to_its_id(self, room: Any) -> None:
        live = room([message("m-1", "2026-01-01T00:00:01Z")])

        joined = await live.join("watercooler")

        assert joined["chat_id"] == "room-3"
        assert "resolved from 'watercooler'" in joined["summary_text"]

    async def test_a_uuid_is_taken_as_given(self, room: Any) -> None:
        """An id needs no lookup, so joining one must not cost a room list."""
        room_id = "3f9a2e51-9d1c-4a7b-8f21-6f0d2b9f4e10"

        assert await room().service.resolve_room(room_id) == room_id

    async def test_an_unknown_name_offers_the_rooms_that_exist(self, room: Any) -> None:
        with pytest.raises(ValueError) as error:
            await room().service.resolve_room("standup")

        guidance = str(error.value)
        assert "'Project Alpha' (id room-1)" in guidance
        assert "'Watercooler' (id room-3)" in guidance
        assert "create" in guidance, (
            "with no match at all, creating the room is the other real option"
        )

    async def test_an_ambiguous_name_asks_which_match_was_meant(
        self, room: Any
    ) -> None:
        with pytest.raises(ValueError) as error:
            await room().service.resolve_room("alpha")

        guidance = str(error.value)
        assert "'Project Alpha' (id room-1)" in guidance
        assert "'Alpha retrospective' (id room-2)" in guidance
        assert "'Watercooler'" not in guidance, (
            "listing rooms that do not match would bury the actual choice"
        )

    async def test_a_failed_join_answers_the_model_rather_than_erroring(
        self, room: Any
    ) -> None:
        """Guidance only helps if the model reads it, so it must be output."""
        result = await room([]).call(RoomTool.JOIN, chat_id="standup")

        assert result.isError
        assert "'Watercooler' (id room-3)" in result.content[0].text
