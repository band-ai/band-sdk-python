"""Unit tests for integration participant helpers (no live API).

Placed outside ``tests/integration/`` so CI's unit job collects them — that tree
is ignored when ``CI=true``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest.core.api_error import ApiError

from tests.integration.participants import (
    SUCCESS,
    absent_from_room,
    try_list_participants,
)


def _client_with_participants(**methods: AsyncMock) -> MagicMock:
    client = MagicMock()
    for name, method in methods.items():
        setattr(client.agent_api_participants, name, method)
    return client


@pytest.mark.asyncio
async def test_try_list_participants_success() -> None:
    client = _client_with_participants(
        list_agent_chat_participants=AsyncMock(return_value=MagicMock(data=[]))
    )
    assert await try_list_participants(client, "room-1") == SUCCESS


@pytest.mark.asyncio
async def test_try_list_participants_maps_404() -> None:
    client = _client_with_participants(
        list_agent_chat_participants=AsyncMock(
            side_effect=ApiError(status_code=404, body="not found")
        )
    )
    assert await try_list_participants(client, "room-1") == "404"


@pytest.mark.asyncio
async def test_absent_from_room_restores_prior_role_after_body() -> None:
    participant = MagicMock(id="agent-2", role="admin")
    list_mock = AsyncMock(
        side_effect=[
            MagicMock(data=[participant]),  # snapshot before remove
            MagicMock(data=[participant]),  # ensure_not_in_room sees them
            MagicMock(data=[]),  # ensure_in_room restore: absent
        ]
    )
    remove_mock = AsyncMock()
    add_mock = AsyncMock()
    client = _client_with_participants(
        list_agent_chat_participants=list_mock,
        remove_agent_chat_participant=remove_mock,
        add_agent_chat_participant=add_mock,
    )

    async with absent_from_room(client, "room-1", "agent-2") as prior:
        assert prior == "admin"
        remove_mock.assert_awaited_once_with("room-1", "agent-2")
        add_mock.assert_not_awaited()

    add_mock.assert_awaited_once()
    await_args = add_mock.await_args
    assert await_args is not None
    participant = await_args.kwargs["participant"]
    assert participant.participant_id == "agent-2"
    assert participant.role == "admin"


@pytest.mark.asyncio
async def test_absent_from_room_restores_even_when_body_raises() -> None:
    participant = MagicMock(id="agent-2", role="member")
    list_mock = AsyncMock(
        side_effect=[
            MagicMock(data=[participant]),
            MagicMock(data=[participant]),
            MagicMock(data=[]),
        ]
    )
    remove_mock = AsyncMock()
    add_mock = AsyncMock()
    client = _client_with_participants(
        list_agent_chat_participants=list_mock,
        remove_agent_chat_participant=remove_mock,
        add_agent_chat_participant=add_mock,
    )

    with pytest.raises(RuntimeError, match="boom"):
        async with absent_from_room(client, "room-1", "agent-2"):
            raise RuntimeError("boom")

    add_mock.assert_awaited_once()
    await_args = add_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["participant"].role == "member"
