"""Unit tests for integration participant helpers (no live API).

Placed outside ``tests/integration/`` so CI's unit job collects them — that tree
is ignored when ``CI=true``.

A small stateful fake stands in for the participants API so the tests assert
observable room state, not the helpers' internal call sequence.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from band_rest.core.api_error import ApiError
from band_rest.types import ParticipantRequest

from tests.integration.participants import absent_from_room, ensure_participant


class FakeParticipantsApi:
    """In-memory participants resource: {participant_id: role} per room."""

    def __init__(self, participants: dict[str, str]) -> None:
        self.participants = dict(participants)
        self.fail_add = False

    async def list_agent_chat_participants(self, chat_id: str) -> Any:
        return SimpleNamespace(
            data=[
                SimpleNamespace(id=pid, role=role)
                for pid, role in self.participants.items()
            ]
        )

    async def add_agent_chat_participant(
        self, chat_id: str, *, participant: ParticipantRequest
    ) -> None:
        if self.fail_add:
            raise ApiError(status_code=500, body="add failed")
        self.participants[participant.participant_id] = participant.role or "member"

    async def remove_agent_chat_participant(
        self, chat_id: str, participant_id: str
    ) -> None:
        self.participants.pop(participant_id)


def fake_client(participants: dict[str, str]) -> Any:
    return SimpleNamespace(agent_api_participants=FakeParticipantsApi(participants))


@pytest.mark.asyncio
async def test_ensure_participant_preserves_existing_role() -> None:
    client = fake_client({"user-1": "owner"})

    await ensure_participant(client, "room-1", "user-1")

    assert client.agent_api_participants.participants == {"user-1": "owner"}


@pytest.mark.asyncio
async def test_absent_from_room_restores_prior_role_after_body() -> None:
    client = fake_client({"agent-2": "admin"})

    async with absent_from_room(client, "room-1", "agent-2"):
        assert "agent-2" not in client.agent_api_participants.participants

    assert client.agent_api_participants.participants == {"agent-2": "admin"}


@pytest.mark.asyncio
async def test_absent_from_room_restores_even_when_body_raises() -> None:
    client = fake_client({"agent-2": "member"})

    with pytest.raises(RuntimeError, match="boom"):
        async with absent_from_room(client, "room-1", "agent-2"):
            raise RuntimeError("boom")

    assert client.agent_api_participants.participants == {"agent-2": "member"}


@pytest.mark.asyncio
async def test_failed_restore_does_not_displace_body_error() -> None:
    """The body's failure stays the reported one when the restore add fails."""
    client = fake_client({"agent-2": "member"})

    with pytest.raises(AssertionError, match="real failure"):
        async with absent_from_room(client, "room-1", "agent-2"):
            client.agent_api_participants.fail_add = True
            raise AssertionError("real failure")
