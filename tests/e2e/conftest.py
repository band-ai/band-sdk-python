"""E2E test configuration and fixtures.

E2E tests run adapters against a real Band platform with real (cheap) LLMs.
They verify platform functionality and integration correctness, not LLM output quality.

Run manually only, never in CI/CD:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

Configuration is loaded from .env.test with E2E-specific overrides from env vars.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError
from thenvoi_rest import AsyncRestClient, ChatRoomRequest
from thenvoi_rest.types import (
    ParticipantRequest,
)
from thenvoi_testing.settings import ThenvoiTestSettings as BandTestSettings

from thenvoi.client.streaming import WebSocketClient

from tests.conftest_integration import is_room_alive
from tests.e2e.helpers import TrackingWebSocketClient

# Load .env.test into os.environ so LLM libraries (langchain, anthropic, etc.)
# can pick up OPENAI_API_KEY, ANTHROPIC_API_KEY, and other keys.
_ENV_TEST_PATH = Path(__file__).parent.parent.parent / ".env.test"
load_dotenv(_ENV_TEST_PATH, override=False)

if TYPE_CHECKING:
    from tests.e2e.adapters.conftest import AdapterFactory

# NOTE: pytestmark in conftest.py is NOT applied to collected tests.
# The 120s timeout is applied via pytest_collection_modifyitems below.

logger = logging.getLogger(__name__)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply E2E-specific markers to all collected tests in this directory.

    1. ``asyncio(loop_scope="session")`` — Fixtures default to the session
       loop via ``asyncio_default_fixture_loop_scope`` in pyproject.toml,
       but test functions default to function-scoped loops. This mismatch
       causes "Future attached to a different loop" errors when tests call
       into session-scoped WS/REST clients.

    2. ``timeout(120)`` — E2E tests interact with live platforms and LLMs,
       so they need more time than the 30s default in pyproject.toml.
       ``pytestmark`` in conftest.py is NOT applied to collected tests;
       markers must be added here or directly on test items.
    """
    e2e_dir = Path(__file__).parent
    session_marker = pytest.mark.asyncio(loop_scope="session")
    timeout_marker = pytest.mark.timeout(120)
    for item in items:
        if Path(item.path).is_relative_to(e2e_dir):
            item.add_marker(session_marker)
            item.add_marker(timeout_marker)


# Platform limits agents to 10 active chat rooms; cap room searches accordingly.
_MAX_ROOMS_TO_SEARCH = 10


# =============================================================================
# E2E Settings
# =============================================================================


class E2ESettings(BandTestSettings):
    """Settings for E2E tests, extending the standard test settings.

    Loads from .env.test and allows E2E-specific overrides via env vars.
    Pydantic BaseSettings automatically maps environment variables to fields
    (e.g. E2E_LLM_MODEL -> e2e_llm_model) with case-insensitive matching.
    """

    # Standard BandTestSettings convention for locating the env file.
    _env_file_path = Path(__file__).parent.parent.parent / ".env.test"

    # E2E-specific settings (override via environment variables)
    e2e_llm_model: str = "gpt-4o-mini"
    e2e_anthropic_model: str = "claude-3-haiku-20240307"
    e2e_timeout: int = 30
    e2e_tests_enabled: bool = False


# =============================================================================
# Skip Markers
# =============================================================================


def _check_e2e_status() -> tuple[bool, str]:
    """Check if E2E tests should be skipped.

    Evaluated once at module import time (when the ``requires_e2e`` marker
    is created). Returns ``(is_disabled, reason)`` so the skip message is
    actionable.
    """
    try:
        settings = E2ESettings()
        if not settings.e2e_tests_enabled:
            return True, "E2E_TESTS_ENABLED is not set to true"
        if not settings.thenvoi_api_key:
            return True, "THENVOI_API_KEY is not set"
        return False, "E2E tests enabled"
    except (ValidationError, ValueError, OSError) as exc:
        logger.warning(
            "E2E settings could not be loaded (missing .env.test?), skipping E2E tests",
            exc_info=True,
        )
        return True, f"E2E settings could not be loaded: {exc}"


_e2e_is_disabled, _e2e_skip_reason = _check_e2e_status()

requires_e2e = pytest.mark.skipif(
    _e2e_is_disabled,
    reason=_e2e_skip_reason or "E2E tests disabled",
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def e2e_config() -> E2ESettings:
    """Provide E2E settings to tests (session-scoped singleton)."""
    return E2ESettings()


@pytest.fixture(scope="session")
def e2e_created_room_ids() -> list[str]:
    """Session-scoped mutable list tracking room IDs created during the E2E run.

    A mutable container is needed because session-scoped fixtures (like the
    room allocator) append to this list during the run, and the room summary
    fixture reads it at teardown.  Using a list (not a set) preserves
    creation order for the summary log.
    """
    return []


@pytest.fixture(scope="session", autouse=True)
def e2e_room_summary(e2e_created_room_ids: list[str]) -> Generator[None, None, None]:
    """Log a summary of rooms created during the E2E test session.

    Rooms persist on the platform (no delete API for agents), so this
    summary helps operators track accumulation across runs.
    """
    yield
    if e2e_created_room_ids:
        logger.info(
            "E2E session created %d room(s) that will persist: %s",
            len(e2e_created_room_ids),
            ", ".join(e2e_created_room_ids),
        )


@pytest.fixture(scope="session")
def e2e_session_client(
    e2e_config: E2ESettings,
) -> AsyncRestClient:
    """Session-scoped REST client shared across all E2E fixtures.

    Avoids creating multiple short-lived AsyncRestClient instances in each
    session-scoped fixture. AsyncRestClient has no close() method — the
    underlying httpx client is managed internally.
    """
    if not e2e_config.thenvoi_api_key:
        pytest.skip("THENVOI_API_KEY not set")

    return AsyncRestClient(
        api_key=e2e_config.thenvoi_api_key,
        base_url=e2e_config.thenvoi_base_url,
    )


@pytest.fixture(scope="session")
def e2e_user_client(
    e2e_config: E2ESettings,
) -> AsyncRestClient:
    """Session-scoped REST client authenticated as the User.

    Used by ``send_trigger_message`` so the trigger comes from the User
    (not the agent). The agent runtime skips self-authored messages, so
    using the agent client would silently fail to trigger processing.
    """
    if not e2e_config.thenvoi_api_key_user:
        pytest.skip("THENVOI_API_KEY_USER not set (needed for user REST client)")

    return AsyncRestClient(
        api_key=e2e_config.thenvoi_api_key_user,
        base_url=e2e_config.thenvoi_base_url,
    )


@pytest.fixture
def api_client(
    e2e_user_client: AsyncRestClient,
) -> AsyncRestClient:
    """Function-scoped alias for the user REST client.

    Tests inject ``api_client`` to send trigger messages. This now
    resolves to the **user**-scoped client so the agent runtime correctly
    processes the incoming message.
    """
    return e2e_user_client


# =============================================================================
# Per-Adapter Room Allocation
# =============================================================================


# Async callable: adapter_name -> (room_id, user_id, user_name)
RoomAllocator = Callable[[str], Awaitable[tuple[str, str, str]]]


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
async def ws_client(
    e2e_config: E2ESettings,
) -> AsyncGenerator[TrackingWebSocketClient, None]:
    """Session-scoped WebSocket client for observing agent responses.

    Connects as the **User** (via ``thenvoi_api_key_user``) rather than
    the agent. The platform enforces one WS connection per agent, so a
    second agent connection would kill the Agent's own connection. The
    User is a room participant and receives the same ``message_created``
    events, making it a safe observer that coexists with the Agent.

    Session-scoped to avoid creating/tearing down a WS connection per test,
    which adds latency and can cause flakiness.

    Wraps the raw WebSocketClient in a TrackingWebSocketClient that tracks
    joined channels and explicitly leaves them on teardown.
    """
    if not e2e_config.thenvoi_api_key_user:
        pytest.skip("THENVOI_API_KEY_USER not set (needed for WS observer)")

    ws = WebSocketClient(
        ws_url=e2e_config.thenvoi_ws_url,
        api_key=e2e_config.thenvoi_api_key_user,
        agent_id=None,  # User connection, not agent
    )

    async with ws:
        tracking_ws = TrackingWebSocketClient(ws)
        yield tracking_ws
        await tracking_ws.cleanup_channels()


@pytest.fixture(
    params=[
        "langgraph",
        "anthropic",
        "pydantic_ai",
        "claude_sdk",
        "crewai",
    ]
)
def adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, AdapterFactory]:
    """Parametrized fixture yielding (name, factory) for each adapter.

    Defined here (e2e/conftest.py) so both adapters/ and scenarios/ tests
    share a single definition. The ADAPTER_FACTORIES import is deferred to
    avoid a circular dependency (adapters/conftest.py imports E2ESettings
    from this module). The ``AdapterFactory`` type is imported under
    ``TYPE_CHECKING`` for the same reason.
    """
    from tests.e2e.adapters.conftest import ADAPTER_FACTORIES

    name: str = request.param
    return name, ADAPTER_FACTORIES[name]
