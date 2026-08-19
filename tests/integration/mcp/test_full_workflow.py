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

from tests.integration.mcp.conftest import (
    LiveHarness,
    _extract_id,
    _unwrap,
    ensure_mentionable_participant,
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
async def test_agent_create_room_send_and_read_back(
    harness: LiveHarness, agent_room: str
) -> None:
    """create_chatroom -> add owner -> send_message -> get_participants round trip."""
    # The room was created by the ``agent_room`` fixture.
    logger.info("Created agent room %s", agent_room)

    owner_id = await ensure_mentionable_participant(harness, agent_room)
    send_result = await harness.call(
        "band_send_message",
        content="integration test message",
        chat_id=agent_room,
        mentions=[owner_id],
    )
    assert send_result is not None, "send_message returned nothing"

    participants = await harness.call("band_get_participants", chat_id=agent_room)
    data = _unwrap(participants)
    assert isinstance(data, list), participants
    logger.info("Room %s has %d participants", agent_room, len(data))


@requires_api
@pytest.mark.asyncio(loop_scope="session")  # see loop_scope note above
async def test_agent_send_message_accepts_room_id_alias(
    harness: LiveHarness, agent_room: str
) -> None:
    """The forward-compat ``room_id`` alias dispatches just like ``chat_id``."""
    owner_id = await ensure_mentionable_participant(harness, agent_room)
    result = await harness.call(
        "band_send_message",
        content="alias path message",
        room_id=agent_room,
        mentions=[owner_id],
    )
    assert result is not None


@requires_api
async def test_human_create_and_get_chat_room(harness: LiveHarness) -> None:
    """Human workflow: create a chat room then fetch it by id."""
    created = await harness.call("band_create_my_chat_room")
    chat_id = _extract_id(created)
    assert chat_id, f"band_create_my_chat_room returned no id: {created!r}"

    fetched = await harness.call("band_get_my_chat_room", chat_id=chat_id)
    assert _extract_id(fetched) == chat_id, fetched
    logger.info("Human created + fetched chat room %s", chat_id)


@requires_api
@pytest.mark.asyncio(loop_scope="session")  # see loop_scope note above
async def test_two_agents_collaborate_in_shared_room(
    harness: LiveHarness, harness_2: LiveHarness, agent_room: str
) -> None:
    """Agent 1 adds a second, genuinely distinct agent identity and @mentions
    them; agent 2 independently confirms membership through its own session,
    not agent 1's participant cache."""
    second_agent_id = get_test_agent_id_2()
    assert second_agent_id, "TEST_AGENT_ID_2 must be set in .env.test"

    await ensure_mentionable_participant(
        harness, agent_room, identifier=second_agent_id
    )
    sent = await harness.call(
        "band_send_message",
        chat_id=agent_room,
        content="hello from agent one",
        mentions=[second_agent_id],
    )
    assert sent is not None, "send_message returned nothing"

    participants = _unwrap(
        await harness_2.call("band_get_participants", chat_id=agent_room)
    )
    assert any(p["id"] == second_agent_id for p in participants), participants
    logger.info("Agent 2 independently confirmed membership in %s", agent_room)
