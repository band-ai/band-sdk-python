"""Brief the agent with its current identity and room communication guidance."""

from __future__ import annotations

from typing import Any

from band.integrations.desktop_app.tools import RoomTool
from band.runtime.tools.inputs.chat import MENTION_GUIDANCE
from tests.integrations.desktop_app.conftest import ROOM_ID, TOM, message


class TestBriefing:
    """The briefing is the whole contract: prompted, not just wired."""

    async def test_opening_refreshes_an_already_cached_agent_identity(
        self, room: Any
    ) -> None:
        live = room([])
        await live.service.viewer()

        await live.join()

        assert live.tools.profile_calls == 2

    async def test_a_remount_speaks_the_agents_current_identity(
        self, room: Any
    ) -> None:
        """Every mounting tool re-briefs the agent, so each re-reads the
        profile: a remount that reused the cached identity would misname the
        agent and mis-match its mentions once the profile changed under a
        long-running process."""
        live = room([message("m-1", "2026-01-01T00:00:01Z")])
        await live.join()

        live.tools.profile = {**TOM, "name": "thomas"}
        remounted = await live.invoke(RoomTool.SHOW, chat_id=ROOM_ID)

        assert remounted["viewer"]["name"] == "thomas"
        assert "You are thomas" in remounted["role_briefing"]

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
        assert MENTION_GUIDANCE in briefing

    async def test_it_orders_the_monitoring_loop_and_reaches_the_model(
        self, room: Any
    ) -> None:
        joined = await room([]).join()

        assert RoomTool.MONITOR in joined["role_briefing"]
        assert joined["role_briefing"] in joined["summary_text"], (
            "the join summary is the only copy the model actually reads"
        )
