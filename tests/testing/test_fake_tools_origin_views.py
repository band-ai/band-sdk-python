"""A FakeAgentTools view calling as another origin shares its room's state."""

from __future__ import annotations

from band.testing import FakeAgentTools

SUBAGENT = "subagent-1"
MESSAGE = {
    "id": "msg-1",
    "content": "hello",
    "sender_id": "user-1",
    "sender_type": "User",
    "message_type": "text",
}


class TestFakeToolsOriginViews:
    async def test_a_view_and_its_original_number_tasks_from_one_board(self) -> None:
        tools = FakeAgentTools()
        view = tools.for_origin(SUBAGENT)

        first = await tools.create_task("Draft the plan")
        second = await view.create_task("Review the plan")

        assert [first["number"], second["number"]] == [1, 2]
        assert view.tasks == tools.tasks

    async def test_room_context_set_on_the_original_is_seen_through_a_view(
        self,
    ) -> None:
        tools = FakeAgentTools()
        view = tools.for_origin(SUBAGENT)

        tools.set_room_context([MESSAGE])
        page = await view.fetch_room_context(room_id=tools.room_id)

        assert [item["content"] for item in page["data"]] == [MESSAGE["content"]]
