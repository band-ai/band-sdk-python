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
from band_rest import AsyncRestClient

from tests.integration.conftest import (
    AgentInfo,
    Attempt,
    PeerInfo,
    absent_from_room,
    ensure_in_room,
    ensure_not_in_room,
    get_participant_role,
    requires_multi_agent,
    try_add,
    try_list_participants,
    try_remove,
)

logger = logging.getLogger(__name__)


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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await try_remove(session_api_client, chat_id, shared_agent2_info.id)
        logger.info("Owner removes member agent: %s", result)
        assert result == Attempt.OK, (
            f"Owner should be able to remove member agent, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await ensure_in_room(
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        result = await try_remove(session_api_client, chat_id, shared_agent2_info.id)
        logger.info("Owner removes admin: %s", result)
        assert result == Attempt.OK, (
            f"Owner should be able to remove admin, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await ensure_in_room(
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
        result = await try_remove(session_api_client, chat_id, shared_user_peer.id)
        logger.info("Owner removes user: %s", result)
        assert result == Attempt.FORBIDDEN, (
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Member removes owner: %s", result)
        assert result == Attempt.FORBIDDEN, (
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        # Verify agent2 is indeed admin
        role = await get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin", f"Agent2 should be admin, got: {role}"

        # Admin (agent2) can remove self (leave as admin)
        result = await try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        logger.info("Admin removes self: %s", result)
        assert result == Attempt.OK, (
            f"Admin should be able to remove self (leave), got: {result}"
        )

        # Restore agent2 for subsequent tests
        await ensure_in_room(
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )

        result = await try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Admin removes owner: %s", result)
        assert result == Attempt.FORBIDDEN, (
            f"Admin should NOT be able to remove owner, got: {result}"
        )

        # Restore agent2 for subsequent tests
        await ensure_in_room(
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await try_remove(session_api_client_2, chat_id, shared_agent1_info.id)
        logger.info("Member removes admin/owner: %s", result)
        assert result == Attempt.FORBIDDEN, (
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        result = await try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        logger.info("Member removes self: %s", result)
        assert result == Attempt.OK, (
            f"Member should be able to remove self (leave), got: {result}"
        )

        # Restore agent2 for subsequent tests
        await ensure_in_room(
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
        result = await try_remove(session_api_client, chat_id, shared_agent1_info.id)
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
        await ensure_not_in_room(session_api_client, chat_id, shared_agent2_info.id)

        result = await try_add(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Owner adds agent as member: %s", result)
        assert result == Attempt.OK, (
            f"Owner should be able to add agent as member, got: {result}"
        )

        role = await get_participant_role(
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
        await ensure_not_in_room(session_api_client, chat_id, shared_agent2_info.id)

        result = await try_add(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )
        logger.info("Owner adds agent as admin: %s", result)
        assert result == Attempt.OK, (
            f"Owner should be able to add agent as admin, got: {result}"
        )

        role = await get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin"

        # Restore to member
        await ensure_in_room(
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
        role = await get_participant_role(
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "admin"
        )
        role = await get_participant_role(
            session_api_client, chat_id, shared_agent2_info.id
        )
        assert role == "admin", f"Agent2 should be admin, got: {role}"

        # 2. Agent2 (admin) leaves the room
        result = await try_remove(session_api_client_2, chat_id, shared_agent2_info.id)
        assert result == Attempt.OK, f"Admin should be able to leave, got: {result}"

        # 3. Agent2 tries to add itself back — room is invisible, expect 404
        result = await try_add(
            session_api_client_2, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Removed agent tries self-add: %s", result)
        assert result == Attempt.NOT_FOUND, (
            f"Removed agent should get 404 when trying to self-add, got: {result}"
        )

        # 4. Restore: owner adds agent2 back
        await ensure_in_room(
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        # Agent2 (member) tries to add itself as admin
        result = await try_add(
            session_api_client_2, chat_id, shared_agent2_info.id, "admin"
        )
        logger.info("Member adds self as admin: %s", result)
        assert result == Attempt.FORBIDDEN, (
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
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )

        # Try to add agent2 again (already a participant)
        result = await try_add(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )
        logger.info("Add duplicate participant: %s", result)
        assert result == Attempt.CONFLICT, (
            f"Adding duplicate participant should return 409, got: {result}"
        )


# =============================================================================
# Remote band-mcp identity boundary (list participants)
# =============================================================================


@requires_multi_agent
@pytest.mark.asyncio(loop_scope="session")
class TestRemoteMcpIdentityBoundary:
    """Room-scoped tools run as the band-mcp identity, not the host adapter's.

    The copilot_docker examples drive tools through a remote band-mcp whose
    ``BAND_AGENT_KEY`` is a Band agent. Listing participants on a host room
    succeeds only when that identity is authorized in the room — the same
    boundary a mismatched MCP key hits with HTTP 404.
    """

    async def test_aligned_identity_can_list_host_room_participants(
        self,
        session_api_client: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent1_info: AgentInfo,
    ) -> None:
        """Host identity (same key as a correctly configured band-mcp) can list."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        assert await try_list_participants(session_api_client, chat_id) == Attempt.OK
        assert (
            await get_participant_role(
                session_api_client, chat_id, shared_agent1_info.id
            )
            is not None
        )

    async def test_foreign_identity_cannot_list_host_room_participants(
        self,
        session_api_client: AsyncRestClient,
        session_api_client_2: AsyncRestClient,
        shared_multi_agent_room: str | None,
        shared_agent2_info: AgentInfo,
    ) -> None:
        """A non-participant MCP identity cannot see the host room (404)."""
        if shared_multi_agent_room is None:
            pytest.skip("shared_multi_agent_room not available")

        chat_id = shared_multi_agent_room
        # Ensure agent2 starts as a member so the CM has a role to restore.
        await ensure_in_room(
            session_api_client, chat_id, shared_agent2_info.id, "member"
        )
        async with absent_from_room(session_api_client, chat_id, shared_agent2_info.id):
            assert (
                await try_list_participants(session_api_client_2, chat_id)
                == Attempt.NOT_FOUND
            )

        assert (
            await get_participant_role(
                session_api_client, chat_id, shared_agent2_info.id
            )
            == "member"
        )
