"""RoomSessions: one Parlant customer and session per Band room."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.parlant.rooms import RoomSessions


@pytest.fixture
def rooms(mock_parlant_server, mock_app) -> RoomSessions:
    return RoomSessions(server=mock_parlant_server, app=mock_app, agent_id="agent-1")


async def test_customer_id_does_not_collide_across_rooms_sharing_a_prefix(
    rooms, mock_parlant_server
):
    """Two rooms sharing a UUID prefix must map to distinct Parlant customers."""
    room_a = "aaaaaaaa-1111-4444-8888-000000000001"
    room_b = "aaaaaaaa-2222-4444-8888-000000000002"

    await rooms.session_for(room_a, customer_name="Alice")
    await rooms.session_for(room_b, customer_name="Bob")

    customer_ids = [
        call.kwargs["id"]
        for call in mock_parlant_server.create_customer.await_args_list
    ]
    assert customer_ids == [f"band-{room_a}", f"band-{room_b}"]


async def test_session_is_reused_until_the_room_is_forgotten(rooms, mock_app):
    first = await rooms.session_for("room-1", customer_name="Alice")
    again = await rooms.session_for("room-1", customer_name="Alice")
    mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-new"))
    rooms.forget("room-1")
    fresh = await rooms.session_for("room-1", customer_name="Alice")

    assert (first, again, fresh) == ("session-123", "session-123", "session-new")


async def test_customer_survives_a_failed_session_create(
    rooms, mock_parlant_server, mock_app
):
    mock_app.sessions.create = AsyncMock(
        side_effect=[RuntimeError("db unreachable"), MagicMock(id="session-1")]
    )

    with pytest.raises(RuntimeError, match="db unreachable"):
        await rooms.session_for("room-1", customer_name="Alice")
    session_id = await rooms.session_for("room-1", customer_name="Alice")

    assert session_id == "session-1"
    mock_parlant_server.create_customer.assert_awaited_once()
