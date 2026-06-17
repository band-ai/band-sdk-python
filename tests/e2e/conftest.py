"""E2E test configuration and fixtures.

E2E tests run adapters against a real Band platform with real (cheap) LLMs.
They verify platform functionality and integration correctness, not LLM output quality.

Run manually only, never in CI/CD:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

Configuration is loaded from .env.test with E2E-specific overrides from env vars.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError, field_validator
from band_rest import AsyncRestClient, ChatRoomRequest
from band_rest.types import (
    ParticipantRequest,
)
from band_testing.settings import BandTestSettings

from band.client.streaming import WebSocketClient
from band.client.streaming.errors import WebSocketUpgradeError

from tests.conftest_integration import is_room_alive
from tests.e2e.helpers import TrackingWebSocketClient

# Load .env.test into os.environ so LLM libraries (langchain, anthropic, etc.)
# can pick up OPENAI_API_KEY, ANTHROPIC_API_KEY, and other keys.
_ENV_TEST_PATH = Path(__file__).parent.parent.parent / ".env.test"
load_dotenv(_ENV_TEST_PATH, override=False)

_PROVIDER_BASE_URL_ENV_VARS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_HOST",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL",
)


@contextmanager
def _cleared_provider_base_url_env_vars() -> Generator[None, None, None]:
    original_values = {
        name: os.environ.get(name) for name in _PROVIDER_BASE_URL_ENV_VARS
    }
    for env_var in _PROVIDER_BASE_URL_ENV_VARS:
        os.environ.pop(env_var, None)
    try:
        yield
    finally:
        for env_var, value in original_values.items():
            if value is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = value


@pytest.fixture(autouse=True)
def _clear_provider_base_url_env_vars_for_live_e2e() -> Generator[None, None, None]:
    """Prevent local provider proxies from affecting live E2E calls.

    The cleanup is scoped to enabled E2E tests and restores the environment after
    each test so collecting this conftest cannot mutate unrelated test lanes.
    """

    if os.environ.get("E2E_TESTS_ENABLED") != "true":
        yield
        return

    with _cleared_provider_base_url_env_vars():
        yield


if TYPE_CHECKING:
    from tests.e2e.adapters.conftest import AdapterFactory

# NOTE: pytestmark in conftest.py is NOT applied to collected tests.
# The 120s timeout is applied via pytest_collection_modifyitems below.

logger = logging.getLogger(__name__)


class _E2ERestRateLimiter:
    def __init__(self, *, requests_per_second: float = 3.0) -> None:
        self._min_interval = 1.0 / requests_per_second
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = self._next_request_at - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_request_at = now + self._min_interval


class _RateLimitedObjectProxy:
    def __init__(self, target: Any, limiter: _E2ERestRateLimiter) -> None:
        self._target = target
        self._limiter = limiter
        self._cache: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if not callable(attr):
            if attr is None or isinstance(
                attr,
                (str, int, float, bool, tuple, list, dict, set),
            ):
                return attr
            if name not in self._cache:
                self._cache[name] = _RateLimitedObjectProxy(attr, self._limiter)
            return self._cache[name]

        async def _call(*args: Any, **kwargs: Any) -> Any:
            await self._limiter.wait()
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        return _call


@pytest.fixture(scope="session")
def e2e_rest_rate_limiter() -> _E2ERestRateLimiter:
    return _E2ERestRateLimiter(requests_per_second=1.0)


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
            if inspect.iscoroutinefunction(getattr(item, "obj", None)):
                item.add_marker(session_marker, append=False)
            if not list(item.iter_markers(name="timeout")):
                item.add_marker(timeout_marker)


# Platform limits agents to 10 active chat rooms; cap room searches accordingly.
_MAX_ROOMS_TO_SEARCH = 10
_DEFAULT_CREATED_ROOM_BUDGET = 10
_CREATED_ROOM_BUDGET_ENV = "E2E_CREATED_ROOM_BUDGET"


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
    e2e_llm_model: str = "gpt-5.4-mini"
    e2e_anthropic_model: str = "claude-haiku-4-5-20251001"
    e2e_timeout: int = 30
    e2e_tests_enabled: bool = False

    @field_validator("e2e_llm_model")
    @classmethod
    def validate_openai_model(cls, value: str) -> str:
        """Reject placeholder model names before live rooms or agents are created."""
        if not value or value.strip() != value:
            raise ValueError("E2E_LLM_MODEL must be a non-empty trimmed model name")
        if "x.x" in value.lower() or "placeholder" in value.lower():
            raise ValueError(
                "E2E_LLM_MODEL must be a concrete model name, not a placeholder"
            )
        return value

    @field_validator("e2e_anthropic_model")
    @classmethod
    def validate_anthropic_model(cls, value: str) -> str:
        """Reject placeholder Anthropic model names before live setup begins."""
        if not value or value.strip() != value:
            raise ValueError(
                "E2E_ANTHROPIC_MODEL must be a non-empty trimmed model name"
            )
        if "x.x" in value.lower() or "placeholder" in value.lower():
            raise ValueError(
                "E2E_ANTHROPIC_MODEL must be a concrete model name, not a placeholder"
            )
        return value


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
        if not settings.band_api_key:
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


def _created_room_budget_from_env() -> int:
    value = os.environ.get(_CREATED_ROOM_BUDGET_ENV)
    if value is None:
        return _DEFAULT_CREATED_ROOM_BUDGET
    try:
        budget = int(value)
    except ValueError as exc:
        raise ValueError(f"{_CREATED_ROOM_BUDGET_ENV} must be an integer") from exc
    if budget < 0:
        raise ValueError(f"{_CREATED_ROOM_BUDGET_ENV} must be >= 0")
    return budget


def _assert_room_creation_budget_available(
    *,
    created_room_ids: list[str],
    budget: int,
    label: str,
) -> None:
    if len(created_room_ids) >= budget:
        pytest.fail(
            f"E2E room creation budget exhausted before creating {label!r}: "
            f"{len(created_room_ids)}/{budget} rooms already created this run. "
            f"Increase {_CREATED_ROOM_BUDGET_ENV} only when the live platform room "
            "cap has enough headroom."
        )


def _track_created_room(
    *,
    created_room_ids: list[str],
    budget: int,
    room_id: str,
    label: str,
) -> None:
    _assert_room_creation_budget_available(
        created_room_ids=created_room_ids,
        budget=budget,
        label=label,
    )
    created_room_ids.append(room_id)


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


@pytest.fixture(scope="session")
def e2e_room_creation_budget() -> int:
    """Maximum persistent rooms this test process may create before failing."""
    return _created_room_budget_from_env()


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
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
) -> AsyncRestClient:
    """Session-scoped REST client shared across all E2E fixtures.

    Avoids creating multiple short-lived AsyncRestClient instances in each
    session-scoped fixture. AsyncRestClient has no close() method — the
    underlying httpx client is managed internally.
    """
    if not e2e_config.band_api_key:
        pytest.skip("THENVOI_API_KEY not set")

    client = AsyncRestClient(
        api_key=e2e_config.band_api_key,
        base_url=e2e_config.band_base_url,
    )
    return cast(
        AsyncRestClient,
        _RateLimitedObjectProxy(client, e2e_rest_rate_limiter),
    )


@pytest.fixture(scope="session")
def e2e_user_client(
    e2e_config: E2ESettings,
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
) -> AsyncRestClient:
    """Session-scoped REST client authenticated as the User.

    Used by ``send_trigger_message`` so the trigger comes from the User
    (not the agent). The agent runtime skips self-authored messages, so
    using the agent client would silently fail to trigger processing.
    """
    if not e2e_config.band_api_key_user:
        pytest.skip("THENVOI_API_KEY_USER not set (needed for user REST client)")

    client = AsyncRestClient(
        api_key=e2e_config.band_api_key_user,
        base_url=e2e_config.band_base_url,
    )
    return cast(
        AsyncRestClient,
        _RateLimitedObjectProxy(client, e2e_rest_rate_limiter),
    )


@pytest.fixture(scope="session")
def e2e_unlimited_user_client(e2e_config: E2ESettings) -> AsyncRestClient:
    """User REST client without the shared E2E rate limiter.

    This exists only for spec-level burst tests where hidden harness pacing would
    weaken the behavior being proven. Ordinary E2E tests should use
    ``e2e_user_client`` or ``api_client``.
    """
    if not e2e_config.band_api_key_user:
        pytest.skip("THENVOI_API_KEY_USER not set (needed for user REST client)")

    return AsyncRestClient(
        api_key=e2e_config.band_api_key_user,
        base_url=e2e_config.band_base_url,
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


@dataclass(frozen=True)
class E2EAgentCredentials:
    agent_id: str
    api_key: str
    name: str


def _adapter_env_prefix(adapter_name: str) -> str:
    return "E2E_" + adapter_name.upper().replace("-", "_")


def _adapter_credentials_from_env(
    adapter_name: str,
) -> E2EAgentCredentials | None:
    prefix = _adapter_env_prefix(adapter_name)
    agent_id = os.environ.get(f"{prefix}_AGENT_ID")
    api_key = os.environ.get(f"{prefix}_AGENT_API_KEY")
    name = os.environ.get(f"{prefix}_AGENT_NAME")
    if not any((agent_id, api_key, name)):
        return None
    missing = [
        env_name
        for env_name, value in (
            (f"{prefix}_AGENT_ID", agent_id),
            (f"{prefix}_AGENT_API_KEY", api_key),
            (f"{prefix}_AGENT_NAME", name),
        )
        if not value
    ]
    if missing:
        pytest.skip(
            f"Incomplete {adapter_name} live agent credentials: missing {', '.join(missing)}"
        )
    return E2EAgentCredentials(
        agent_id=cast(str, agent_id),
        api_key=cast(str, api_key),
        name=cast(str, name),
    )


@pytest.fixture
async def e2e_adapter_agent_credentials(
    adapter_entry: tuple[str, AdapterFactory],
    e2e_config: E2ESettings,
    e2e_session_client: AsyncRestClient,
) -> E2EAgentCredentials:
    """Agent credentials for the current adapter lane.

    Live matrix runs can set ``E2E_<ADAPTER>_AGENT_ID``,
    ``E2E_<ADAPTER>_AGENT_API_KEY``, and ``E2E_<ADAPTER>_AGENT_NAME`` to
    isolate each adapter on its own stable Band identity. If unset, the shared
    legacy ``TEST_AGENT_ID``/``THENVOI_API_KEY`` identity is used.
    """
    adapter_name, _factory = adapter_entry
    adapter_credentials = _adapter_credentials_from_env(adapter_name)
    if adapter_credentials is not None:
        return adapter_credentials

    agent_me = await e2e_session_client.agent_api_identity.get_agent_me()
    return E2EAgentCredentials(
        agent_id=agent_me.data.id,
        api_key=e2e_config.band_api_key,
        name=agent_me.data.name,
    )


@pytest.fixture(scope="session")
async def e2e_user_peer(e2e_session_client: AsyncRestClient) -> Any:
    """Owner User peer cached once for live E2E room setup."""
    peers_response = await e2e_session_client.agent_api_peers.list_agent_peers(
        page_size=100,
    )
    user_peer = next((p for p in peers_response.data if p.type == "User"), None)
    if user_peer is None:
        pytest.skip("No User peer available for E2E tests")
    return user_peer


@pytest.fixture(scope="session")
async def e2e_room_allocator(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
) -> RoomAllocator:
    """Lazy per-adapter room allocator (session-scoped).

    Returns an async function ``allocate(name) -> (room_id, user_id, user_name)``
    that assigns a dedicated room to each adapter. Reuses existing rooms from
    prior runs where possible; creates new rooms only when needed.

    The platform limits agents to 10 active rooms, and rooms persist (no delete
    API). Each adapter gets its own room to avoid cross-adapter contamination
    in room history. Adapter-owned lanes use their own allocator below because
    a session-client room can be visible to the owner without waking the adapter
    identity under test.
    """
    client = e2e_session_client
    cache: dict[str, tuple[str, str, str]] = {}

    user_peer = e2e_user_peer

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
        _assert_room_creation_budget_available(
            created_room_ids=e2e_created_room_ids,
            budget=e2e_room_creation_budget,
            label=name,
        )
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
        _track_created_room(
            created_room_ids=e2e_created_room_ids,
            budget=e2e_room_creation_budget,
            room_id=room_id,
            label=name,
        )
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
async def e2e_fresh_room(
    e2e_session_client: AsyncRestClient,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
) -> tuple[str, str, str]:
    """Create a fresh room for live scenarios whose assertions depend on clean history."""
    user_peer = e2e_user_peer

    _assert_room_creation_budget_available(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        label="fresh session-client room",
    )
    response = await e2e_session_client.agent_api_chats.create_agent_chat(
        chat=ChatRoomRequest()
    )
    if response.data is None:
        pytest.fail("create_agent_chat returned no data")
    room_id = response.data.id
    await e2e_session_client.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=user_peer.id, role="member"),
    )
    _track_created_room(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        room_id=room_id,
        label="fresh session-client room",
    )
    logger.info("E2E: Created fresh room %s", room_id)
    return room_id, user_peer.id, user_peer.name


@pytest.fixture(scope="session")
def e2e_adapter_room_cache() -> dict[tuple[str, str], tuple[str, str, str]]:
    """Rooms created for adapter-matrix lanes during this E2E session."""

    return {}


async def _create_adapter_owned_room(
    *,
    label: str,
    e2e_config: E2ESettings,
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
    e2e_adapter_agent_credentials: E2EAgentCredentials,
) -> tuple[str, str, str]:
    """Create a room through the current adapter agent identity.

    Session-client rooms are visible to the owner, but they are not guaranteed to
    wake the adapter identity under test. Adapter lanes must allocate through the
    same agent credentials that will process the room.
    """
    _assert_room_creation_budget_available(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        label=label,
    )
    client = cast(
        AsyncRestClient,
        _RateLimitedObjectProxy(
            AsyncRestClient(
                api_key=e2e_adapter_agent_credentials.api_key,
                base_url=e2e_config.band_base_url,
            ),
            e2e_rest_rate_limiter,
        ),
    )
    response = await client.agent_api_chats.create_agent_chat(chat=ChatRoomRequest())
    if response.data is None:
        pytest.fail(f"create_agent_chat returned no data for {label}")
    room_id = response.data.id
    await client.agent_api_participants.add_agent_chat_participant(
        room_id,
        participant=ParticipantRequest(participant_id=e2e_user_peer.id, role="member"),
    )
    _track_created_room(
        created_room_ids=e2e_created_room_ids,
        budget=e2e_room_creation_budget,
        room_id=room_id,
        label=label,
    )
    logger.info("E2E: Created adapter-owned room %s for '%s'", room_id, label)
    return room_id, e2e_user_peer.id, e2e_user_peer.name


@pytest.fixture
async def e2e_adapter_room(
    adapter_entry: tuple[str, AdapterFactory],
    e2e_config: E2ESettings,
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
    e2e_adapter_agent_credentials: E2EAgentCredentials,
    e2e_adapter_room_cache: dict[tuple[str, str], tuple[str, str, str]],
) -> tuple[str, str, str]:
    """Session-reused room owned by the current parametrized adapter identity.

    The shared adapter matrix can run each framework under a distinct Band agent.
    A room created by one agent does not wake a different agent, so each lane
    gets a room keyed by adapter name and agent id. Reusing that room avoids
    creating more persistent platform rooms than the live matrix can afford.
    """
    name, _ = adapter_entry
    cache_key = (name, e2e_adapter_agent_credentials.agent_id)
    if cache_key in e2e_adapter_room_cache:
        return e2e_adapter_room_cache[cache_key]

    result = await _create_adapter_owned_room(
        label=name,
        e2e_config=e2e_config,
        e2e_rest_rate_limiter=e2e_rest_rate_limiter,
        e2e_created_room_ids=e2e_created_room_ids,
        e2e_room_creation_budget=e2e_room_creation_budget,
        e2e_user_peer=e2e_user_peer,
        e2e_adapter_agent_credentials=e2e_adapter_agent_credentials,
    )
    e2e_adapter_room_cache[cache_key] = result
    return result


@pytest.fixture
async def e2e_fresh_adapter_room(
    adapter_entry: tuple[str, AdapterFactory],
    e2e_config: E2ESettings,
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
    e2e_adapter_agent_credentials: E2EAgentCredentials,
) -> tuple[str, str, str]:
    """Fresh room owned by the current adapter identity for clean live scenarios."""
    name, _ = adapter_entry
    return await _create_adapter_owned_room(
        label=f"{name}:fresh",
        e2e_config=e2e_config,
        e2e_rest_rate_limiter=e2e_rest_rate_limiter,
        e2e_created_room_ids=e2e_created_room_ids,
        e2e_room_creation_budget=e2e_room_creation_budget,
        e2e_user_peer=e2e_user_peer,
        e2e_adapter_agent_credentials=e2e_adapter_agent_credentials,
    )


@pytest.fixture
async def e2e_parlant_room(
    e2e_room_allocator: RoomAllocator,
) -> tuple[str, str, str]:
    """Dedicated room for Parlant adapter tests."""
    return await e2e_room_allocator("parlant")


@pytest.fixture
async def e2e_isolation_room_b(
    adapter_entry: tuple[str, AdapterFactory],
    e2e_config: E2ESettings,
    e2e_rest_rate_limiter: _E2ERestRateLimiter,
    e2e_created_room_ids: list[str],
    e2e_room_creation_budget: int,
    e2e_user_peer: Any,
    e2e_adapter_agent_credentials: E2EAgentCredentials,
    e2e_adapter_room_cache: dict[tuple[str, str], tuple[str, str, str]],
) -> tuple[str, str, str]:
    """Second room for isolation tests, owned by the current adapter identity.

    Room B cannot come from the session-client allocator: a user-owned room may
    not wake the adapter identity under test. It is cached per adapter identity
    so isolation tests get two wakeable rooms without creating a new Room B for
    every test method.
    """
    name, _ = adapter_entry
    cache_key = (f"{name}:isolation_b", e2e_adapter_agent_credentials.agent_id)
    if cache_key in e2e_adapter_room_cache:
        return e2e_adapter_room_cache[cache_key]
    result = await _create_adapter_owned_room(
        label=f"{name}:isolation_b",
        e2e_config=e2e_config,
        e2e_rest_rate_limiter=e2e_rest_rate_limiter,
        e2e_created_room_ids=e2e_created_room_ids,
        e2e_room_creation_budget=e2e_room_creation_budget,
        e2e_user_peer=e2e_user_peer,
        e2e_adapter_agent_credentials=e2e_adapter_agent_credentials,
    )
    e2e_adapter_room_cache[cache_key] = result
    return result


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

    Connects as the **User** (via ``band_api_key_user``) rather than
    the agent. The platform enforces one WS connection per agent, so a
    second agent connection would kill the Agent's own connection. The
    User is a room participant and receives the same ``message_created``
    events, making it a safe observer that coexists with the Agent.

    Session-scoped to avoid creating/tearing down a WS connection per test,
    which adds latency and can cause flakiness.

    Wraps the raw WebSocketClient in a TrackingWebSocketClient that tracks
    joined channels and explicitly leaves them on teardown.
    """
    if not e2e_config.band_api_key_user:
        pytest.skip("THENVOI_API_KEY_USER not set (needed for WS observer)")

    for attempt in range(4):
        ws = WebSocketClient(
            ws_url=e2e_config.band_ws_url,
            api_key=e2e_config.band_api_key_user,
            agent_id=None,  # User connection, not agent
        )
        try:
            async with ws:
                tracking_ws = TrackingWebSocketClient(ws)
                yield tracking_ws
                await tracking_ws.cleanup_channels()
                return
        except WebSocketUpgradeError as exc:
            if exc.status_code != 429 or attempt == 3:
                raise
            retry_after = exc.retry_after or 5 * (attempt + 1)
            logger.warning(
                "E2E observer WebSocket hit HTTP 429; retrying in %ss",
                retry_after,
            )
            await asyncio.sleep(retry_after)


DEFAULT_E2E_ADAPTERS = (
    "langgraph",
    "anthropic",
    "pydantic_ai",
    "claude_sdk",
    "opencode",
    "codex",
    "letta",
)


@pytest.fixture(params=DEFAULT_E2E_ADAPTERS)
def adapter_entry(
    request: pytest.FixtureRequest,
) -> tuple[str, AdapterFactory]:
    """Parametrized fixture yielding (name, factory) for each adapter.

    Defined here (e2e/conftest.py) so both adapters/ and scenarios/ tests
    share a single definition. CrewAI is intentionally excluded from this default
    parametrized lane because its dependencies conflict with the dev environment;
    run CrewAI coverage in the separate dev-crewai lane. The ADAPTER_FACTORIES
    import is deferred to avoid a circular dependency (adapters/conftest.py
    imports E2ESettings from this module). The ``AdapterFactory`` type is
    imported under ``TYPE_CHECKING`` for the same reason.
    """
    from tests.e2e.adapters.conftest import ADAPTER_FACTORIES

    name: str = request.param
    return name, ADAPTER_FACTORIES[name]
