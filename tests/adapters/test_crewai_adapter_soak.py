"""Soak test: 100 sequential on_message calls across 3 mocked rooms.

Asserts:
- No exceptions across the run
- nest_asyncio.apply is invoked at most once (lazy patch idempotency)
- Per-room state in `_message_history` does not leak between rooms
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.core.types import PlatformMessage


class MockBaseTool:
    name: str = ""
    description: str = ""

    def __init__(self) -> None:
        pass


@pytest.fixture
def crewai_mocks(monkeypatch):
    mock_crewai_module = MagicMock()
    mock_crewai_tools_module = MagicMock()
    mock_nest_asyncio = MagicMock()

    mock_crewai_module.Agent = MagicMock()
    mock_crewai_module.LLM = MagicMock()
    mock_crewai_tools_module.BaseTool = MockBaseTool

    for mod in (
        "thenvoi.adapters.crewai",
        "thenvoi.integrations.crewai",
        "thenvoi.integrations.crewai.runtime",
        "thenvoi.integrations.crewai.tools",
    ):
        sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "crewai", mock_crewai_module)
    monkeypatch.setitem(sys.modules, "crewai.tools", mock_crewai_tools_module)
    monkeypatch.setitem(sys.modules, "nest_asyncio", mock_nest_asyncio)

    try:
        yield {"crewai": mock_crewai_module, "nest_asyncio": mock_nest_asyncio}
    finally:
        for mod in (
            "thenvoi.adapters.crewai",
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)


def _make_msg(idx: int, room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=f"msg-{idx}",
        room_id=room_id,
        content=f"hello {idx}",
        sender_id="user-1",
        sender_type="User",
        sender_name="Pat",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_soak_100_turns_3_rooms(crewai_mocks):
    """Drive 100 on_message calls across 3 rooms; assert no leaks."""
    import importlib

    module = importlib.import_module("thenvoi.adapters.crewai")
    CrewAIAdapter = module.CrewAIAdapter

    adapter = CrewAIAdapter(model="gpt-4o-mini")
    fake_agent = MagicMock()
    fake_agent.kickoff_async = AsyncMock(return_value=MagicMock(raw="ok"))
    adapter._crewai_agent = fake_agent
    adapter.agent_name = "tester"
    adapter.agent_description = "soak"

    rooms = ["room-A", "room-B", "room-C"]
    tools_per_room = {r: AsyncMock() for r in rooms}

    for i in range(100):
        room_id = rooms[i % 3]
        msg = _make_msg(i, room_id)
        await adapter.on_message(
            msg=msg,
            tools=tools_per_room[room_id],
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=(i < 3),
            room_id=room_id,
        )

    # Per-room state present, no cross-room leakage: each room has only its own
    # message ids in its history.
    for room_id in rooms:
        room_history = adapter._message_history.get(room_id, [])
        assert room_history, f"room {room_id} has no recorded history"
        # Every "user" entry's content must reference one of THIS room's messages,
        # not another room's. _process_message stores messages with the user's
        # original content via msg.format_for_llm(), so we check the ids by
        # iteration count: each room saw 100/3 turns (approximately), so a non-
        # empty history is the floor; the strong check is leakage.
        # All histories were populated only via on_message(room_id=...), so
        # leakage would manifest as content from a turn that targeted a
        # different room. We approximate by requiring history length matches
        # turns dispatched to that room.
        expected_turns = sum(1 for i in range(100) if rooms[i % 3] == room_id)
        # Each turn appends 1 user entry; bootstrap turns may append history
        # if any was passed (we passed []), so exactly expected_turns user
        # entries plus assistant entries when result.raw is set.
        user_entries = [e for e in room_history if e["role"] == "user"]
        assert len(user_entries) == expected_turns, (
            f"room {room_id} user-entry count={len(user_entries)} "
            f"expected={expected_turns}"
        )

    # nest_asyncio.apply must not be called from this path — Crew sync tools
    # are not invoked during on_message in this test.
    assert crewai_mocks["nest_asyncio"].apply.call_count <= 1
