"""Shared Agent-API participant state helpers for integration tests."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band_rest import AsyncRestClient
from band_rest.core.api_error import ApiError
from band_rest.types import ParticipantRequest

logger = logging.getLogger(__name__)


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


async def ensure_participant(
    client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
    role: str = "member",
) -> None:
    """Ensure a participant is present without changing an existing role."""
    current_role = await get_participant_role(client, chat_id, participant_id)
    if current_role is not None:
        return
    await client.agent_api_participants.add_agent_chat_participant(
        chat_id,
        participant=ParticipantRequest(participant_id=participant_id, role=role),
    )
    logger.info("Ensured participant %s in room %s", participant_id, chat_id)


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


@asynccontextmanager
async def absent_from_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> AsyncIterator[None]:
    """Temporarily remove a participant; restore their prior role on exit.

    Always restores when a prior role existed. If the body raised, a failing
    restore is logged instead of raised so it never displaces the body's
    exception as the reported test failure.
    """
    previous_role = await get_participant_role(owner_client, chat_id, participant_id)
    if previous_role is not None:
        await owner_client.agent_api_participants.remove_agent_chat_participant(
            chat_id, participant_id
        )
        logger.info("Removed %s from room %s", participant_id, chat_id)

    async def restore() -> None:
        if previous_role is not None:
            await ensure_in_room(
                owner_client, chat_id, participant_id, role=previous_role
            )

    try:
        yield
    except BaseException:
        try:
            await restore()
        except ApiError:
            logger.exception(
                "Restore of %s in room %s failed after body error",
                participant_id,
                chat_id,
            )
        raise
    await restore()
