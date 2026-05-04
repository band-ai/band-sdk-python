"""Integration tests for thenvoi-trigger CLI against a real API.

Tests the trigger flow end-to-end: peer lookup, room creation,
participant addition, and message delivery using real REST endpoints.

Run with:
    uv run pytest tests/integration/test_trigger.py -v -s

Requires .env.test credentials (THENVOI_API_KEY at minimum).
"""

from __future__ import annotations

import argparse
import logging

import pytest

from thenvoi.cli.trigger import (
    _format_api_error,
    find_peer_by_handle,
    run,
)
from thenvoi_rest import AsyncRestClient
from thenvoi_rest.core.api_error import ApiError

from tests.conftest_integration import (
    AgentInfo,
    get_api_key,
    get_base_url,
    get_user_api_key,
    is_no_clean_mode,
    requires_api,
    requires_user_api,
)

logger = logging.getLogger(__name__)


def _make_args(**overrides) -> argparse.Namespace:
    """Create a Namespace with sensible defaults pointing at the real API."""
    defaults = {
        "api_key": get_api_key(),
        "rest_url": get_base_url(),
        "auth_mode": "agent",
        "target_handle": None,  # must be supplied per test
        "message": "Integration test trigger message",
        "timeout": 120,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Agent-mode happy path
# ---------------------------------------------------------------------------


@requires_api
class TestTriggerAgentMode:
    """Agent-mode integration tests using real agent API credentials."""

    async def test_agent_mode_happy_path(
        self,
        api_client: AsyncRestClient,
        shared_agent1_info: AgentInfo | None,
        request: pytest.FixtureRequest,
    ) -> None:
        """Full trigger flow: peer lookup -> create room -> add participant -> send message."""
        assert api_client is not None
        assert shared_agent1_info is not None

        # Find a peer to target (use any available peer)
        peers_response = await api_client.agent_api_peers.list_agent_peers()
        assert peers_response.data and len(peers_response.data) > 0, (
            "Agent needs at least one peer for trigger test"
        )
        target_peer = peers_response.data[0]
        target_handle = getattr(target_peer, "handle", None)
        assert target_handle, "Target peer must have a handle"

        logger.info(
            "Triggering with target: %s (handle=%s)", target_peer.name, target_handle
        )

        args = _make_args(
            target_handle=target_handle,
            message="Integration test: agent-mode trigger",
        )

        room_id = await run(args)

        assert room_id, "run() should return a room ID"
        logger.info("Trigger created room: %s", room_id)

        # Verify room exists and has the target as participant
        participants_response = (
            await api_client.agent_api_participants.list_agent_chat_participants(
                chat_id=room_id
            )
        )
        participant_ids = {p.id for p in (participants_response.data or [])}
        assert target_peer.id in participant_ids, (
            f"Target peer {target_peer.id} should be a participant in {room_id}"
        )

        # Verify message was sent
        context_response = await api_client.agent_api_context.get_agent_chat_context(
            chat_id=room_id
        )
        messages = context_response.data or []
        assert len(messages) > 0, "Room should have at least one message"
        assert any("Integration test" in getattr(m, "content", "") for m in messages), (
            "Trigger message should be in room context"
        )

        logger.info("Agent-mode happy path passed (room=%s)", room_id)

        # Cleanup: delete the room unless no-clean mode
        if not is_no_clean_mode(request):
            try:
                await api_client.agent_api_chats.delete_agent_chat(id=room_id)
                logger.info("Cleaned up room %s", room_id)
            except Exception:
                logger.warning("Failed to clean up room %s", room_id, exc_info=True)

    async def test_peer_not_found_raises_value_error(
        self,
        api_client: AsyncRestClient,
    ) -> None:
        """Trigger with a nonexistent handle should raise ValueError."""
        args = _make_args(
            target_handle="@nonexistent-owner/nonexistent-agent-12345",
            message="This should fail",
        )

        with pytest.raises(ValueError, match="not found"):
            await run(args)

    async def test_find_peer_by_handle_against_real_api(
        self,
        api_client: AsyncRestClient,
    ) -> None:
        """find_peer_by_handle should resolve a real peer from the API."""
        peers_response = await api_client.agent_api_peers.list_agent_peers()
        assert peers_response.data and len(peers_response.data) > 0

        target = peers_response.data[0]
        target_handle = getattr(target, "handle", None)
        assert target_handle, "First peer must have a handle"

        # Look up via the trigger's find_peer_by_handle
        real_client = AsyncRestClient(
            api_key=get_api_key(),
            base_url=get_base_url(),
        )
        try:
            result = await find_peer_by_handle(real_client, target_handle, "agent")
        finally:
            await real_client._client_wrapper.httpx_client.httpx_client.aclose()

        assert result is not None, f"Should find peer with handle '{target_handle}'"
        assert result["id"] == target.id
        logger.info("Resolved peer: %s -> %s", target_handle, result["id"])

    async def test_real_api_error_format(
        self,
        api_client: AsyncRestClient,
    ) -> None:
        """Trigger a real ApiError and verify _format_api_error extracts the message."""
        # Use an invalid participant_id to trigger a real API error
        # First create a room we can use
        from thenvoi_rest import ChatRoomRequest

        chat_response = await api_client.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest()
        )
        room_id = chat_response.data.id

        try:
            from thenvoi_rest.types import ParticipantRequest

            with pytest.raises(ApiError) as exc_info:
                await api_client.agent_api_participants.add_agent_chat_participant(
                    chat_id=room_id,
                    participant=ParticipantRequest(
                        participant_id="00000000-0000-0000-0000-000000000000"
                    ),
                )

            err = exc_info.value
            formatted = _format_api_error(err, "add participant")

            # Should NOT fall back to generic "HTTP <code>" — the real API
            # returns structured error bodies that _format_api_error should parse
            logger.info("Formatted API error: %s", formatted)
            assert formatted.startswith("Failed to add participant:"), (
                f"Expected formatted error, got: {formatted}"
            )
        finally:
            try:
                await api_client.agent_api_chats.delete_agent_chat(id=room_id)
            except Exception:
                logger.warning("Failed to clean up room %s", room_id, exc_info=True)


# ---------------------------------------------------------------------------
# User-mode happy path
# ---------------------------------------------------------------------------


@requires_user_api
class TestTriggerUserMode:
    """User-mode integration tests using real user API credentials."""

    async def test_user_mode_happy_path(
        self,
        user_api_client: AsyncRestClient,
        request: pytest.FixtureRequest,
    ) -> None:
        """Full user-mode trigger flow against real human_api endpoints."""
        assert user_api_client is not None

        # Find a peer visible to the user
        try:
            peers_response = await user_api_client.human_api_peers.list_my_peers()
        except Exception as exc:
            # Human API may require Enterprise plan
            if "plan_required" in str(exc) or "403" in str(exc):
                pytest.skip("Human API requires Enterprise plan")
            raise
        assert peers_response.data and len(peers_response.data) > 0, (
            "User needs at least one peer for trigger test"
        )
        target_peer = peers_response.data[0]
        target_handle = getattr(target_peer, "handle", None)
        assert target_handle, "Target peer must have a handle"

        logger.info(
            "User-mode trigger with target: %s (handle=%s)",
            target_peer.name,
            target_handle,
        )

        args = _make_args(
            api_key=get_user_api_key(),
            auth_mode="user",
            target_handle=target_handle,
            message="Integration test: user-mode trigger",
        )

        room_id = await run(args)

        assert room_id, "run() should return a room ID"
        logger.info("User-mode trigger created room: %s", room_id)

        # Cleanup
        if not is_no_clean_mode(request):
            try:
                await user_api_client.human_api_chats.delete_my_chat_room(id=room_id)
                logger.info("Cleaned up user-mode room %s", room_id)
            except Exception:
                logger.warning("Failed to clean up room %s", room_id, exc_info=True)
