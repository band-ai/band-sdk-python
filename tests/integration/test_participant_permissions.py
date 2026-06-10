"""Participant permission tests using pre-existing agents from .env.test.

Uses 2 pre-configured agents to test critical permission paths:
- Agent 1 (session_api_client) owns the shared room
- Agent 2 (session_api_client_2) is managed as admin or member per test

All tests operate on the session-scoped shared_multi_agent_room to stay
within the platform's chat room limit.

Note: Agent API cannot remove User participants from rooms (403), so
user-related tests only verify add and presence, not removal.

History: The previous version used 4 dynamically created agents (via the
Enterprise-only Human API) to cover all role permutations. This rewrite uses
2 pre-existing agents to eliminate the Enterprise plan dependency. See the
class docstrings for specific coverage gaps that require 3+ agents.

Run with: uv run pytest tests/integration/test_participant_permissions.py -v -s
"""

from __future__ import annotations

import logging

import pytest
from thenvoi_rest import AsyncRestClient
from thenvoi_rest.core.api_error import ApiError
from thenvoi_rest.types import ParticipantRequest

from tests.integration.conftest import (
    AgentInfo,
    PeerInfo,
    requires_multi_agent,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helpers
# =============================================================================


async def _get_participant_role(
    client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> str | None:
    """Get the current role of a participant, or None if not in room."""
    response = await client.agent_api_participants.list_agent_chat_participants(chat_id)
    participants = response.data or []
    participant = next((p for p in participants if p.id == participant_id), None)
    return participant.role if participant else None


async def _ensure_in_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
    role: str = "member",
) -> None:
    """Ensure agent participant is in room with the specified role.

    Adds if missing, removes and re-adds if role differs.
    Only works for agent participants (not users).

    If the add fails after removal, attempts to restore the previous role
    so that subsequent tests aren't left with corrupted fixture state.
    """
    current_role = await _get_participant_role(owner_client, chat_id, participant_id)
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
        # Restore the previous role so test state isn't corrupted
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


async def _ensure_not_in_room(
    owner_client: AsyncRestClient,
    chat_id: str,
    participant_id: str,
) -> None:
    """Ensure agent participant is NOT in room.

    Only works for agent participants (not users).
    """
    current_role = await _get_participant_role(owner_client, chat_id, participant_id)
    if current_role is not None:
        await owner_client.agent_api_participants.remove_agent_chat_participant(
            chat_id, participant_id
        )
        logger.info("Removed %s from room %s", participant_id, chat_id)


async def _try_remove(client: AsyncRestClient, chat_id: str, target_id: str) -> str:
    """Try to remove a participant and return the result.

    Returns:
        "success" if removal succeeded
        "403" if forbidden
        "404" if not found
        "409" if conflict

    Raises:
        ApiError: For unexpected status codes.
    """
    try:
        await client.agent_api_participants.remove_agent_chat_participant(
            chat_id, target_id
        )
        return "success"
    except ApiError as e:
        if e.status_code == 403:
            return "403"
        if e.status_code == 404:
            return "404"
        if e.status_code == 409:
            return "409"
        raise


async def _try_add(
    client: AsyncRestClient, chat_id: str, target_id: str, role: str = "member"
) -> str:
    """Try to add a participant and return the result.

    Returns:
        "success" if add succeeded
        "403" if forbidden
        "404" if room not found (e.g. caller is not a participant)
        "409" if already a participant

    Raises:
        ApiError: For unexpected status codes.
    """
    try:
        await client.agent_api_participants.add_agent_chat_participant(
            chat_id,
            participant=ParticipantRequest(participant_id=target_id, role=role),
        )
        return "success"
    except ApiError as e:
        if e.status_code == 403:
            return "403"
        if e.status_code == 404:
            return "404"
        if e.status_code == 409:
            return "409"
        raise


# =============================================================================
# Agent Removal Permission Tests
# =============================================================================


@requires_multi_agent
@pytest.mark.asyncio(loop_scope="session")
class TestParticipantRemovalPermissions:
    """Test participant removal permission paths using shared_multi_agent_room.

    Agent 1 (owner of room), Agent 2 (added with varying roles).
    Each test sets up the required state, runs the assertion, then restores.

    Coverage gaps (require 3+ agents to test, not available with 2):
    - admin removes member agent
    - admin removes other admin
    - member removes other member agent
    - member removes other member user
    TODO(INT-38): Expand when 3+ pre-existing agents are available.
    """

    async def test_owner_removes_member_agent(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Owner removes member agent -> expect success."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await _try_remove(session_api_client, chat_id, shared_agent2_info.id)
        logger.info("Owner removes member agent: %s", result)
        assert result == "success", (
            f"Owner should be able to remove member agent, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_owner_removes_admin_agent(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Owner removes admin agent -> expect success."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        result = await _try_remove(session_api_client, chat_id, shared_agent2_info.id)
        logger.info("Owner removes admin: %s", result)
        assert result == "success", (
            f"Owner should be able to remove admin, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_owner_cannot_remove_user(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_user_peer: PeerInfo | None,
    ):
        """Agent (even owner) cannot remove a User participant -> expect 403.

        Platform restriction: agents cannot remove users from rooms.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")
        if shared_user_peer is None:
            pytest.skip("No User peer available")

        chat_id = shared_multi_agent_room
        result = await _try_remove(session_api_client, chat_id, shared_user_peer.id)
        logger.info("Owner removes user: %s", result)
        assert result == "403", (
            f"Agent should NOT be able to remove user participant, got: {result}"
        )

    async def test_member_cannot_remove_owner(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
        shared_agent2_info: AgentInfo,
    ):
        """Member (agent2) cannot remove owner (agent1) -> expect 403."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await _try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Member removes owner: %s", result)
        assert result == "403", (
            f"Member should NOT be able to remove owner, got: {result}"
        )

    async def test_admin_can_remove_self(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
        shared_agent2_info: AgentInfo,
    ):
        """Admin (agent2) can remove self (leave room) -> expect success.

        Promotes agent2 to admin and verifies it can leave the room.

        Note: With only 2 agents we can't test admin-removes-member-agent
        because agent1 is always the owner and its role can't be changed.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        # Verify agent2 is indeed admin
        role = await _get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin", f"Agent2 should be admin, got: {role}"

        # Admin (agent2) can remove self (leave as admin)
        result = await _try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        logger.info("Admin removes self: %s", result)
        assert result == "success", (
            f"Admin should be able to remove self (leave), got: {result}"
        )

        # Restore agent2 for subsequent tests
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_admin_cannot_remove_owner(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
        shared_agent2_info: AgentInfo,
    ):
        """Admin (agent2) cannot remove owner (agent1) -> expect 403."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        result = await _try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Admin removes owner: %s", result)
        assert result == "403", (
            f"Admin should NOT be able to remove owner, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_member_cannot_remove_admin(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
        shared_agent2_info: AgentInfo,
    ):
        """Member (agent2) cannot remove owner/admin (agent1) -> expect 403.

        With only 2 agents, agent1 is always the owner (highest privilege).
        This verifies that a member cannot remove someone with admin-or-above role.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await _try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Member removes admin/owner: %s", result)
        assert result == "403", (
            f"Member should NOT be able to remove admin/owner, got: {result}"
        )

    async def test_member_can_remove_self(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Member (agent2) can remove self (leave room) -> expect success."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await _try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        logger.info("Member removes self: %s", result)
        assert result == "success", (
            f"Member should be able to remove self (leave), got: {result}"
        )

        # Restore agent2 for subsequent tests
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_owner_cannot_remove_self(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
    ):
        """Owner cannot remove themselves from room -> expect 403 or 409.

        Room owners must transfer ownership before leaving.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        result = await _try_remove(session_api_client, chat_id, shared_agent1_info.id)
        logger.info("Owner removes self: %s", result)
        assert result in ("403", "409"), (
            f"Owner should NOT be able to remove self, got: {result}"
        )


# =============================================================================
# Agent Add Permission Tests
# =============================================================================


@requires_multi_agent
@pytest.mark.asyncio(loop_scope="session")
class TestParticipantAddPermissions:
    """Test participant add permission paths using shared_multi_agent_room.

    Agent 1 (owner of room). Tests manage agent2 participant state.
    User-add tests verify presence only (agents cannot remove users
    to reset state).

    Coverage gaps (require 3+ agents to test, not available with 2):
    - admin adds agent as member (tested indirectly via admin-adds-self-back)
    - admin adds agent as admin
    - member adds agent as member
    - member adds user as member
    TODO(INT-38): Expand when 3+ pre-existing agents are available.
    """

    async def test_owner_adds_agent_as_member(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Owner adds agent as member -> expect success."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_not_in_room(session_api_client, chat_id, shared_agent2_info.id)

        result = await _try_add(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Owner adds agent as member: %s", result)
        assert result == "success", (
            f"Owner should be able to add agent as member, got: {result}"
        )

        role = await _get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "member"

    async def test_owner_adds_agent_as_admin(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Owner adds agent as admin -> expect success."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_not_in_room(session_api_client, chat_id, shared_agent2_info.id)

        result = await _try_add(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )
        logger.info("Owner adds agent as admin: %s", result)
        assert result == "success", (
            f"Owner should be able to add agent as admin, got: {result}"
        )

        role = await _get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin"

        # Restore to member
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_user_is_present_as_member(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_user_peer: PeerInfo | None,
    ):
        """Verify the User peer is a participant (added by conftest).

        Agents can add users to rooms (verified by conftest fixture setup),
        but cannot remove them, so we verify presence only.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")
        if shared_user_peer is None:
            pytest.skip("No User peer available")

        chat_id = shared_multi_agent_room
        role = await _get_participant_role(
            session_api_client, chat_id, shared_user_peer.id
        )
        assert role is not None, "User peer should be a participant in the room"
        logger.info("User peer is present with role: %s", role)

    async def test_removed_agent_cannot_self_add(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """An agent that left a room cannot add itself back -> expect 404.

        Once an agent is no longer a participant, the platform returns 404
        because the room is invisible to that agent. Re-adding requires
        action from a current participant (owner/admin).

        Steps:
        1. Ensure agent2 is admin
        2. Agent2 (admin) leaves the room
        3. Agent2 tries to add itself back -> 404
        4. Owner re-adds agent2 (cleanup)
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room

        # 1. Ensure agent2 is admin
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )
        role = await _get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin", f"Agent2 should be admin, got: {role}"

        # 2. Agent2 (admin) leaves the room
        result = await _try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        assert result == "success", f"Admin should be able to leave, got: {result}"

        # 3. Agent2 tries to add itself back — room is invisible, expect 404
        result = await _try_add(
            session_api_client_2, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Removed agent tries self-add: %s", result)
        assert result == "404", (
            f"Removed agent should get 404 when trying to self-add, got: {result}"
        )

        # 4. Restore: owner adds agent2 back
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

    async def test_member_cannot_add_agent_as_admin(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Member (agent2) cannot self-escalate to admin -> expect 403.

        Agent2 is a member and attempts to re-add itself with admin role,
        which should be rejected since members cannot elevate privileges.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        # Agent2 (member) tries to add itself as admin
        result = await _try_add(
            session_api_client_2, chat_id, shared_agent2_info.id, "admin"
        )
        logger.info("Member adds self as admin: %s", result)
        assert result == "403", (
            f"Member should NOT be able to elevate to admin, got: {result}"
        )

    async def test_add_duplicate_participant_returns_409(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ):
        """Adding a participant who is already in the room -> expect 409.

        Verifies the platform rejects duplicate add requests with a conflict.
        """
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        await _ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        # Try to add agent2 again (already a participant)
        result = await _try_add(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Add duplicate participant: %s", result)
        assert result == "409", (
            f"Adding duplicate participant should return 409, got: {result}"
        )
