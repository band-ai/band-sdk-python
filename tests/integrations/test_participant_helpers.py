"""Unit tests for integration participant helpers (no live API).

Placed outside ``tests/integration/`` so CI's unit job collects them — that tree
is ignored when ``CI=true``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest.types import ParticipantRequest

from tests.integration.participants import absent_from_room, ensure_participant


def _client_with_participants(**methods: AsyncMock) -> MagicMock:
    client = MagicMock()
    for name, method in methods.items():
        setattr(client.agent_api_participants, name, method)
    return client


def _added_participant(add_mock: AsyncMock) -> ParticipantRequest:
    """Projection of the restore/add call — what the helper asked the API to do."""
    assert add_mock.await_args is not None
    return add_mock.await_args.kwargs["participant"]


@pytest.mark.asyncio
async def test_ensure_participant_preserves_existing_role() -> None:
    participant = MagicMock(id="user-1", role="owner")
    list_mock = AsyncMock(return_value=MagicMock(data=[participant]))
    remove_mock = AsyncMock()
    add_mock = AsyncMock()
    client = _client_with_participants(
        list_agent_chat_participants=list_mock,
        remove_agent_chat_participant=remove_mock,
        add_agent_chat_participant=add_mock,
    )

    await ensure_participant(client, "room-1", "user-1")

    remove_mock.assert_not_awaited()
    add_mock.assert_not_awaited()


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

    async with absent_from_room(client, "room-1", "agent-2"):
        remove_mock.assert_awaited_once_with("room-1", "agent-2")
        add_mock.assert_not_awaited()

    assert _added_participant(add_mock) == ParticipantRequest(
        participant_id="agent-2", role="admin"
    )


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

    assert _added_participant(add_mock) == ParticipantRequest(
        participant_id="agent-2", role="member"
    )
