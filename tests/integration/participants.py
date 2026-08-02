"""Shared Agent-API participant helpers for integration tests.

Membership mutators and status-mapped try helpers used by permission tests
and by fixture setup. Resource teardown for temporary absence goes through
:func:`absent_from_room` so shared rooms are not left dirty on assertion failure.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum

from band_rest import AsyncRestClient
from band_rest.core.api_error import ApiError
from band_rest.types import ParticipantRequest

logger = logging.getLogger(__name__)


class Attempt(StrEnum):
    """Outcome of a participant API call that may fail with a known status."""

    OK = "success"
    FORBIDDEN = "403"
    NOT_FOUND = "404"
    CONFLICT = "409"


async def get_participant_role(
    client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> str | None:
    """Return the participant's role, or None if they are not in the room."""
    response = await client.agent_api_participants.list_agent_chat_participants(chat_id)
    participants = response.data or []
    participant = next((p for p in participants if p.id == participant_id), None)
    return participant.role if participant else None


async def ensure_in_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
    role: str = "member",
) -> None:
    """Ensure an agent participant is in the room with the given role.

    Adds if missing, removes and re-adds if the role differs. Only works for
    agent participants (not users).

    If the add fails after removal, attempts to restore the previous role so
    subsequent tests are not left with corrupted fixture state.
    """
    current_role = await get_participant_role(owner_client, chat_id, participant_id)
    if current_role == role:
        return
    if current_role is not None:
        await owner_client.agent_api_participants.remove_agent_chat_participant(
            chat_id, participant_id
        )
    try:
        await owner_client.agent_api_participants.add_agent_chat_participant(
            chat_id,
            participant=ParticipantRequest(participant_id=participant_id, role=role),
        )
    except ApiError:
        if current_role is not None:
            logger.warning(
                "Add failed for %s as %s, restoring previous role %s",
                participant_id,
                role,
                current_role,
            )
            try:
                await owner_client.agent_api_participants.add_agent_chat_participant(
                    chat_id,
                    participant=ParticipantRequest(
                        participant_id=participant_id, role=current_role
                    ),
                )
            except ApiError:
                logger.error(
                    "Restore also failed for %s, room state may be corrupted",
                    participant_id,
                )
        raise
    logger.info("Ensured %s in room %s as %s", participant_id, chat_id, role)


async def ensure_not_in_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> None:
    """Ensure an agent participant is not in the room.

    Only works for agent participants (not users).
    """
    current_role = await get_participant_role(owner_client, chat_id, participant_id)
    if current_role is not None:
        await owner_client.agent_api_participants.remove_agent_chat_participant(
            chat_id, participant_id
        )
        logger.info("Removed %s from room %s", participant_id, chat_id)


def _status_or_raise(exc: ApiError) -> Attempt:
    match exc.status_code:
        case 403:
            return Attempt.FORBIDDEN
        case 404:
            return Attempt.NOT_FOUND
        case 409:
            return Attempt.CONFLICT
        case _:
            raise exc


async def try_remove(client: AsyncRestClient, chat_id: str, target_id: str) -> Attempt:
    """Try to remove a participant; return :class:`Attempt` outcome."""
    try:
        await client.agent_api_participants.remove_agent_chat_participant(
            chat_id, target_id
        )
        return Attempt.OK
    except ApiError as e:
        return _status_or_raise(e)


async def try_add(
    client: AsyncRestClient, chat_id: str, target_id: str, role: str = "member"
) -> Attempt:
    """Try to add a participant; return :class:`Attempt` outcome."""
    try:
        await client.agent_api_participants.add_agent_chat_participant(
            chat_id,
            participant=ParticipantRequest(participant_id=target_id, role=role),
        )
        return Attempt.OK
    except ApiError as e:
        return _status_or_raise(e)


async def try_list_participants(client: AsyncRestClient, chat_id: str) -> Attempt:
    """Try to list participants; return :class:`Attempt` outcome.

    A non-participant caller typically sees :attr:`Attempt.NOT_FOUND` (room
    invisible) — the same boundary remote band-mcp hits when its identity is
    not in the host room.
    """
    try:
        await client.agent_api_participants.list_agent_chat_participants(chat_id)
        return Attempt.OK
    except ApiError as e:
        return _status_or_raise(e)


@asynccontextmanager
async def absent_from_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> AsyncIterator[str | None]:
    """Temporarily remove a participant; restore their prior role on exit.

    Yields the role they had before removal (or ``None`` if they were already
    absent). Always restores in ``finally`` when a prior role existed.
    """
    previous_role = await get_participant_role(owner_client, chat_id, participant_id)
    await ensure_not_in_room(owner_client, chat_id, participant_id)
    try:
        yield previous_role
    finally:
        if previous_role is not None:
            await ensure_in_room(
                owner_client, chat_id, participant_id, role=previous_role
            )
