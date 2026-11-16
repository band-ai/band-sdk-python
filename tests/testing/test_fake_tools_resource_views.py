"""A FakeAgentTools resource view calls as its owner and shares the room's state."""

from __future__ import annotations

import asyncio

from band.core.turn_lifecycle import TurnTask, resource_tools
from band.testing import FakeAgentTools

#: A room message in the fake's canonical form, so it reads back unchanged.
MESSAGE = {
    "id": "msg-1",
    "content": "hello",
    "sender_id": "user-1",
    "sender_type": "User",
    "sender_name": None,
    "message_type": "text",
    "metadata": {"delegation": None},
    "inserted_at": None,
    "created_at": None,
}


def resource_view(tools: FakeAgentTools) -> tuple[FakeAgentTools, TurnTask]:
    """``tools`` as the work running this test sees them, and that work."""
    task = asyncio.current_task()
    assert task is not None
    owner = TurnTask(task)
    view = resource_tools(tools, owner)
    assert isinstance(view, FakeAgentTools)
    return view, owner


class TestFakeToolsResourceViews:
    async def test_a_view_calls_as_its_owner(self) -> None:
        tools = FakeAgentTools()

        view, owner = resource_view(tools)

        assert (tools.origin, view.origin) == (None, owner)

    async def test_a_view_and_its_original_number_tasks_from_one_board(self) -> None:
        tools = FakeAgentTools()
        view, _ = resource_view(tools)

        first = await tools.create_task("Draft the plan")
        second = await view.create_task("Review the plan")

        assert [first["number"], second["number"]] == [1, 2]
        assert view.tasks == tools.tasks

    async def test_room_context_set_on_the_original_is_seen_through_a_view(
        self,
    ) -> None:
        tools = FakeAgentTools()
        view, _ = resource_view(tools)

        tools.set_room_context([MESSAGE])
        page = await view.fetch_room_context(room_id=tools.room_id)

        assert page == {
            "data": [MESSAGE],
            "meta": {"page": 1, "page_size": 50, "total_count": 1, "total_pages": 1},
        }
