"""Joining a room: finding it, and what the agent is told once inside."""

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


class TestBriefing:
    """The briefing is the whole contract: prompted, not just wired."""

    async def test_it_states_who_the_agent_is_and_who_it_works_for(
        self, room: Any
    ) -> None:
        joined = await room([message("m-1", "2026-01-01T00:00:01Z")]).join()
        briefing = joined["role_briefing"]

        assert "You are tom (@alexander.zaikman/tom)" in briefing
        assert "Tom the cat" in briefing
        assert "The human you work for in this room is Alexander Zaikman" in briefing
        assert "jery (@alexander.zaikman/jery, agent, member)" in briefing
        assert "tom (agent, member)" not in briefing, (
            "the agent must not be introduced to itself as one of its peers"
        )

    async def test_it_hands_over_the_exact_mention_handles(self, room: Any) -> None:
        briefing = (await room([]).join())["role_briefing"]

        assert "@alexander.zaikman/jery" in briefing
        assert "@alexander.zaikman" in briefing
        assert "Never type a mention marker into the message content" in briefing

    async def test_it_orders_the_monitoring_loop_and_reaches_the_model(
        self, room: Any
    ) -> None:
        joined = await room([]).join()

        assert RoomTool.MONITOR in joined["role_briefing"]
        assert joined["role_briefing"] in joined["summary_text"], (
            "the join summary is the only copy the model actually reads"
        )


class TestMentionRendering:
    async def test_stored_markers_are_shown_as_handles(self, room: Any) -> None:
        """Raw `@[[id]]` teaches the agent to write it back, which Band cannot resolve."""
        greeting = message("m-1", "2026-01-01T00:00:01Z")
        greeting["content"] = "hi @[[jerry-1]] and @[[human-1]]"

        joined = await room([greeting]).join()

        assert joined["messages"][0]["content"] == (
            "hi @alexander.zaikman/jery and @alexander.zaikman"
        )
