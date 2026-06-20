"""Room allocation + agent identity fixtures for E2E tests.

Two allocators: ``e2e_room_allocator`` reuses rooms across runs (to respect the
platform's 10-room cap) and ``e2e_fresh_room_allocator`` makes a clean room per
test and leaves it on teardown. Plus per-adapter room fixtures and the agent
identity lookups used to @mention agents in trigger messages.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import pytest
from band_rest import AsyncRestClient, ChatRoomRequest
from band_rest.types import ParticipantRequest

from tests.conftest_integration import is_no_clean_mode, is_room_alive
from tests.e2e.settings import RoomAllocator

if TYPE_CHECKING:
    from tests.e2e.adapters.conftest import AdapterFactory

logger = logging.getLogger(__name__)

# Platform limits agents to 10 active chat rooms; cap room searches accordingly.
_MAX_ROOMS_TO_SEARCH = 10


@pytest.fixture(scope="session")
async def e2e_room_allocator(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
) -> RoomAllocator:
    """Lazy per-adapter room allocator (session-scoped).

    Returns an async function ``allocate(name) -> (room_id, user_id, user_name)``
    that assigns a dedicated room to each adapter. Reuses existing rooms from
    prior runs where possible; creates new rooms only when needed.

    The platform limits agents to 10 active rooms, and rooms persist (no delete
    API). Each adapter gets its own room to avoid cross-adapter contamination
    in room history. Expected allocation: 5 standard adapters + 1 Parlant +
    1 isolation Room B = 7 rooms max (well within the 10-room limit).
    """
    client = e2e_session_client
    cache: dict[str, tuple[str, str, str]] = {}

    # Find User peer once
    peers_response = await client.agent_api_peers.list_agent_peers()
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for E2E tests")

    # Collect existing rooms that are alive and already have this User peer.
    # Rooms can be auto-deleted by the platform's 10-room limit, so we
    # validate each room before considering it reusable.
    chats_response = await client.agent_api_chats.list_agent_chats()
    available_rooms: list[str] = []
    for room in (chats_response.data or [])[:_MAX_ROOMS_TO_SEARCH]:
        if not await is_room_alive(client, room.id):
            logger.warning("E2E: Room %s is deleted, skipping", room.id)
            continue
        participants_response = (
            await client.agent_api_participants.list_agent_chat_participants(room.id)
        )
        participant_ids = [p.id for p in (participants_response.data or [])]
        if user_peer.id in participant_ids:
            available_rooms.append(room.id)

    logger.info(
        "E2E: Found %d existing room(s) with User peer %s",
        len(available_rooms),
        user_peer.name,
    )

    used_room_ids: set[str] = set()

    async def allocate(name: str) -> tuple[str, str, str]:
        if name in cache:
            return cache[name]

        # Try to reuse an unassigned existing room
        for room_id in available_rooms:
            if room_id not in used_room_ids:
                used_room_ids.add(room_id)
                result = (room_id, user_peer.id, user_peer.name)
                cache[name] = result
                logger.info("E2E: Reusing room %s for '%s'", room_id, name)
                return result

        # No existing room available — create one
        response = await client.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest()
        )
        if response.data is None:
            pytest.fail("create_agent_chat returned no data")
        room_id = response.data.id
        await client.agent_api_participants.add_agent_chat_participant(
            room_id,
            participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
        )
        used_room_ids.add(room_id)
        e2e_created_room_ids.append(room_id)
        result = (room_id, user_peer.id, user_peer.name)
        cache[name] = result
        logger.info(
            "E2E: Created room %s for '%s' (will persist, no delete API)",
            room_id,
            name,
        )
        return result

    return allocate


@pytest.fixture
async def e2e_fresh_room_allocator(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
    request: pytest.FixtureRequest,
) -> AsyncGenerator[RoomAllocator, None]:
    """Allocate a brand-new room on every call, then leave it on teardown.

    Unlike ``e2e_room_allocator`` (which reuses rooms), this always creates a
    fresh room so the agent starts with a clean, uncontaminated history — use it
    for tests sensitive to prior room content (e.g. memory tests).

    On teardown the agent is removed from each created room so they don't count
    against its 10-room cap (there's no chat-delete API; removing the agent
    participant frees the slot). Opt out with ``--no-clean`` /
    ``BAND_TEST_NO_CLEAN`` to leave the rooms intact for debugging.
    """
    client = e2e_session_client

    peers_response = await client.agent_api_peers.list_agent_peers()
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for E2E tests")
    agent_me = await client.agent_api_identity.get_agent_me()
    agent_id = agent_me.data.id

    created: list[str] = []

    async def allocate(name: str) -> tuple[str, str, str]:
        response = await client.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(title=f"e2e-{name}")
        )
        if response.data is None:
            pytest.fail("create_agent_chat returned no data")
        room_id = response.data.id
        # Record for teardown immediately: the room (with the agent in it) now
        # exists on the platform, so it must be cleaned up even if adding the
        # user participant below fails — otherwise it leaks against the cap.
        e2e_created_room_ids.append(room_id)
        created.append(room_id)
        await client.agent_api_participants.add_agent_chat_participant(
            room_id,
            participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
        )
        logger.info("E2E: Created fresh room %s for '%s'", room_id, name)
        return room_id, user_peer.id, user_peer.name

    yield allocate

    if is_no_clean_mode(request):
        return
    for room_id in created:
        with contextlib.suppress(Exception):
            await client.agent_api_participants.remove_agent_chat_participant(
                room_id, agent_id
            )


@pytest.fixture
async def e2e_adapter_room(
    adapter_entry: tuple[str, AdapterFactory],
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for the current parametrized adapter.

    Returns (room_id, user_id, user_name). Each adapter gets its own room
    to avoid cross-adapter contamination in room history.
    """
    name, _ = adapter_entry
    return await e2e_room_allocator(name)


@pytest.fixture
async def e2e_parlant_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for Parlant adapter tests."""
    return await e2e_room_allocator("parlant")


@pytest.fixture
async def e2e_isolation_room_b(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Shared Room B for room isolation tests.

    All adapters' isolation tests share this as their second room.
    Room A is the adapter's own room (``e2e_adapter_room``).
    """
    return await e2e_room_allocator("_isolation_b")


@pytest.fixture(scope="session")
async def e2e_agent_id(e2e_session_client: AsyncRestClient) -> str:
    """Get the agent ID for the test agent (cached for the entire session).

    Note: Session-scoped because the agent ID is stable for a given API key
    and never changes mid-run. If the underlying agent is recreated between
    tests, this cached value would be stale — but that scenario doesn't
    apply to E2E runs against a persistent platform.
    """
    agent_me = await e2e_session_client.agent_api_identity.get_agent_me()
    return agent_me.data.id


@pytest.fixture(scope="session")
async def e2e_agent_info(e2e_session_client: AsyncRestClient) -> tuple[str, str]:
    """Get (agent_id, agent_name) for the test agent.

    Used by tests that need to @mention the agent in trigger messages.
    """
    agent_me = await e2e_session_client.agent_api_identity.get_agent_me()
    return agent_me.data.id, agent_me.data.name


@pytest.fixture(scope="session")
async def e2e_agent_info_2(
    e2e_session_client_2: AsyncRestClient,
) -> tuple[str, str]:
    """Get (agent_id, agent_name) for the second test agent.

    Used by multi-agent tests to @mention the second agent and to verify it
    was added to / removed from a room.
    """
    agent_me = await e2e_session_client_2.agent_api_identity.get_agent_me()
    return agent_me.data.id, agent_me.data.name


@pytest.fixture(
    params=[
        "langgraph",
        "anthropic",
        "pydantic_ai",
        "claude_sdk",
        "crewai",
        "agno",
    ]
)
def adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, AdapterFactory]:
    """Parametrized fixture yielding (name, factory) for each adapter.

    The ADAPTER_FACTORIES import is deferred to avoid a circular dependency
    (adapters/conftest.py imports E2ESettings from the e2e conftest). The
    ``AdapterFactory`` type is imported under ``TYPE_CHECKING`` for the same
    reason.
    """
    from tests.e2e.adapters.conftest import ADAPTER_FACTORIES

    name: str = request.param
    return name, ADAPTER_FACTORIES[name]
