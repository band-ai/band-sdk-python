"""Live-API workflow tests for the SDK-driven registrar.

Drive a small end-to-end agent workflow through the registrar: create a chat
room, send a message into it, then read participants back — all via
``mcp._tool_manager.call_tool`` against a real Band API. These mutate data on
the test account, so they only run when ``BAND_API_KEY`` is set and the key
serves the agent scope. Run with:

    uv run --all-packages pytest tests/integration/mcp/test_full_workflow.py -v -s --no-cov
"""

from __future__ import annotations

import logging

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tests.integration.mcp.conftest import (
    LiveHarness,
    _extract_id,
    _unwrap,
    add_room_owner,
    get_test_agent_id_2,
    requires_api,
)

logger = logging.getLogger(__name__)


@requires_api
# loop_scope="session" matches asyncio_default_fixture_loop_scope: the async
# `agent_room` fixture and this test must share one event loop, or the
# StandaloneResolver's asyncio.Lock (bound on first use inside agent_room) raises
# "bound to a different event loop" when the test's own harness.call() runs.
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_room_with_human_and_second_agent(
    harness: LiveHarness, second_agent_harness: LiveHarness, agent_room: str
) -> None:
    """A room with more than one real participant, human and agent alike.

    create_chatroom -> add the human owner AND a second, genuinely distinct
    agent identity -> mention both in one send_message -> read participants
    back. The second agent then confirms its own membership through its own
    independent session, not agent 1's participant cache.
    """
    logger.info("Created agent room %s", agent_room)

    owner_id = await add_room_owner(harness, agent_room)
    second_agent_id = get_test_agent_id_2()
    assert second_agent_id, "TEST_AGENT_ID_2 must be set in .env.test"
    await harness.call(
        "band_add_participant", chat_id=agent_room, identifier=second_agent_id
    )

    send_result = await harness.call(
        "band_send_message",
        content="integration test message",
        chat_id=agent_room,
        mentions=[owner_id, second_agent_id],
    )
    assert send_result is not None, "send_message returned nothing"

    participants = _unwrap(
        await harness.call("band_get_participants", chat_id=agent_room)
    )
    assert {p["id"] for p in participants} >= {owner_id, second_agent_id}, participants
    logger.info("Room %s has %d participants", agent_room, len(participants))

    participants_from_second_agent = _unwrap(
        await second_agent_harness.call("band_get_participants", chat_id=agent_room)
    )
    assert any(p["id"] == second_agent_id for p in participants_from_second_agent)


@requires_api
@pytest.mark.asyncio(loop_scope="session")  # see loop_scope note above
async def test_agent_send_message_accepts_room_id_alias(
    harness: LiveHarness, agent_room: str
) -> None:
    """The forward-compat ``room_id`` alias dispatches just like ``chat_id``."""
    owner_id = await add_room_owner(harness, agent_room)
    result = await harness.call(
        "band_send_message",
        content="alias path message",
        room_id=agent_room,
        mentions=[owner_id],
    )
    assert result is not None


@requires_api
@pytest.mark.asyncio(loop_scope="session")  # see loop_scope note above
async def test_agent_send_message_retry_after_lookup_peers(
    harness: LiveHarness, agent_room: str
) -> None:
    """A mention naming a real peer who is not yet a room participant fails
    against the live API; adding them first and retrying the identical call
    then succeeds.

    ``band_lookup_peers`` finds a real candidate (the room-owning human,
    same as ``add_room_owner``'s own lookup) who genuinely is not yet a
    member of this fresh room -- the failure below reflects the live
    participant list, not a made-up id.
    """
    peers = _unwrap(
        await harness.call(
            "band_lookup_peers", chat_id=agent_room, page=1, page_size=100
        )
    )
    candidate_id = next(p for p in peers if p["type"] == "User")["id"]

    with pytest.raises(ToolError, match=f"Unknown participant '{candidate_id}'"):
        await harness.call(
            "band_send_message",
            content="should fail: not yet a participant",
            chat_id=agent_room,
            mentions=[candidate_id],
        )

    await harness.call(
        "band_add_participant", chat_id=agent_room, identifier=candidate_id
    )

    retried = await harness.call(
        "band_send_message",
        content="should succeed: now a participant",
        chat_id=agent_room,
        mentions=[candidate_id],
    )
    assert retried is not None, "retried send_message returned nothing"


@requires_api
async def test_human_create_and_get_chat_room(harness: LiveHarness) -> None:
    """Human workflow: create a chat room then fetch it by id."""
    created = await harness.call("band_create_my_chat_room")
    chat_id = _extract_id(created)
    assert chat_id, f"band_create_my_chat_room returned no id: {created!r}"

    fetched = await harness.call("band_get_my_chat_room", chat_id=chat_id)
    assert _extract_id(fetched) == chat_id, fetched
    logger.info("Human created + fetched chat room %s", chat_id)
