"""Pytest fixtures for Band SDK tests.

Most fixtures are provided by the thenvoi_testing package.

Available from thenvoi_testing:
- factory: MockDataFactory for creating test data
- mock_agent_api, mock_human_api, mock_api_client: API client mocks
- mock_websocket: WebSocket client mock
- fake_agent_tools: FakeAgentTools for adapter testing
- sample_room_message, sample_agent_message: Message payloads

This file contains SDK-specific fixtures and event helpers that must
return SDK-native types for pattern matching compatibility.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from functools import cache
from itertools import count
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.client.rest import AsyncRestClient

from band.client.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)
from band.platform.event import (
    MessageEvent,
    RoomAddedEvent,
    RoomDeletedEvent,
    RoomRemovedEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)
from band.runtime.types import PlatformMessage

from tests.paths import ENV_TEST_FILE

# Enable the `pytester` fixture (must live in the root conftest) so hook/plugin behaviour
# can be exercised in a real sub-run — used by tests/e2e/baseline/guards/test_agent_wiring.py.
pytest_plugins = ["pytester"]

# Env-var prefixes that adapter config classes (CodexAdapterConfig,
# LettaAdapterConfig, OpencodeAdapterConfig) self-source from.
_ADAPTER_CONFIG_ENV_PREFIXES = ("CODEX_", "LETTA_", "OPENCODE_")


def pytest_configure(config: pytest.Config) -> None:
    """Block ``.env.test``'s adapter-config keys before collection can leak them.

    ``tests/e2e/baseline/settings.py`` calls ``load_dotenv(ENV_TEST_FILE,
    override=False)`` at import time; several subdirectory ``conftest.py``
    modules (e.g. ``tests/docker/conftest.py``,
    ``tests/framework_conformance/conftest.py``) import it transitively, at
    whichever indeterminate, collection-order-dependent moment they happen to
    load. Any ``CODEX_``/``LETTA_``/``OPENCODE_`` key it would set is one the
    adapter config classes above now read by default, so a contributor's
    local ``.env.test`` would otherwise leak into unrelated unit tests'
    "no override" defaults, and inconsistently depending on whether a given
    default was baked before or after that key got set.

    Reserving each such key as an empty string here — before any subdirectory
    conftest can run — makes the later ``load_dotenv(override=False)`` skip
    it (already present), while ``env_ignore_empty=True`` on those settings
    classes treats an empty value the same as unset.

    Skipped entirely when ``E2E_TESTS_ENABLED`` is set: a live E2E run wants
    ``.env.test``'s real values (e.g. ``CODEX_CWD``), not neutralized ones.
    """
    if os.environ.get("E2E_TESTS_ENABLED", "").lower() == "true":
        return
    for key in dotenv_values(ENV_TEST_FILE):
        if key.startswith(_ADAPTER_CONFIG_ENV_PREFIXES) and key not in os.environ:
            os.environ[key] = ""


class CollectionGateSettings(BaseSettings):
    """Env-var gates for which marked suites collect/run.

    Field name == env var (case-insensitive). Read via a fresh instance in the
    collection hooks so the current environment is always what decides.
    ``env_ignore_empty`` treats a set-but-empty gate (``CI=``, as some CI
    wrappers export) as unset instead of raising a ValidationError that would
    kill the whole run inside a collection hook.
    """

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    ci: bool = False  # CI
    e2e_tests_enabled: bool = False  # E2E_TESTS_ENABLED
    docker_tests_enabled: bool = False  # DOCKER_TESTS_ENABLED
    sandbox_tests_enabled: bool = False  # SANDBOX_TESTS_ENABLED
    vscode_chat_tests_enabled: bool = False  # VSCODE_CHAT_TESTS_ENABLED


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    """Skip real-API integration tests (tests/integration/) in CI.

    Matches the exact path segment: the substring check it replaces also
    swallowed tests/integrations/ — the mocked framework-integration unit
    tests — silently dropping them from every CI run. Returns None (not
    False) when not ignoring, so other mechanisms like --ignore still
    apply locally.
    """
    if CollectionGateSettings().ci and "integration" in collection_path.parts:
        return True
    return None


# Opt-in suite gates: marker -> the CollectionGateSettings field that opens it.
# The env var IS the field name uppercased (the pydantic-settings contract), so
# the skip reason is derived — one row here is all a new gated suite needs.
GATED_MARKERS: dict[str, str] = {
    "e2e": "e2e_tests_enabled",
    "docker_build": "docker_tests_enabled",
    "sandbox": "sandbox_tests_enabled",
    "vscode_chat": "vscode_chat_tests_enabled",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip gate-marked suites unless their env gate is explicitly enabled.

    tests/e2e/ gates itself through its own conftest; this covers marked
    tests living elsewhere (e.g. the codex ACP protocol tests). These
    suites spawn real backends, real `docker build`s, sbx microVMs, or a
    live VS Code window — none may ride a normal unit run, and none can
    rely on mere tool availability (CI runners do have Docker), so each
    needs its explicit opt-in.
    """
    gates = CollectionGateSettings()
    closed = {
        marker: pytest.mark.skip(
            reason=f"set {field.upper()}=true to run {marker}-marked tests"
        )
        for marker, field in GATED_MARKERS.items()
        if not getattr(gates, field)
    }
    for item in items:
        for marker, skip in closed.items():
            if item.get_closest_marker(marker):
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_single_instance_lock(request, tmp_path_factory, monkeypatch):
    """Give every unit test its own single-instance lock dir.

    The guard is host-global by design (one process per agent id); unit
    tests reuse agent ids and may start runtimes they never stop, which
    would otherwise hold the shared lock for the rest of the pytest
    process. The lock dir is minted lazily (per test, on first guard
    construction) so the 3000+ tests that never build a guard pay nothing.

    Live tests (e2e/integration) keep the REAL host-global guard: there,
    two same-id agents genuinely corrupt each other, and a loud
    BandConfigError beats silent message stealing.
    """
    if {"e2e", "integration"} & set(request.node.path.parts):
        yield
        return

    from band.runtime.single_instance import SingleInstanceGuard

    lock_dir: list = []
    created: list[SingleInstanceGuard] = []

    def isolated_guard(agent_id):
        if not lock_dir:
            lock_dir.append(tmp_path_factory.mktemp("agent-locks"))
        guard = SingleInstanceGuard(agent_id, lock_dir=lock_dir[0])
        created.append(guard)
        return guard

    monkeypatch.setattr(
        "band.runtime.platform_runtime.SingleInstanceGuard", isolated_guard
    )
    yield
    # A test that starts a runtime and never stops it would otherwise leak
    # the lock fd (and its process-registry entry) for the whole session.
    for guard in created:
        guard.release()


@pytest.fixture(autouse=True)
def isolated_adapter_config_env(request, monkeypatch):
    """Defense-in-depth: strip adapter-config env vars before each unit test.

    ``pytest_configure`` above closes the ``.env.test`` leak at its source;
    this additionally protects against a test that sets one of these vars
    directly (``monkeypatch.setenv`` without cleanup, a subprocess, etc.)
    from bleeding into an unrelated later test's "no override" defaults.

    Live tests (e2e/integration) keep the real environment: there, those
    values are the point.
    """
    if {"e2e", "integration"} & set(request.node.path.parts):
        yield
        return

    for key in list(os.environ):
        if key.startswith(_ADAPTER_CONFIG_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def assert_no_leaked_adapter_config_env() -> None:
    """Fail loudly if a CODEX_/LETTA_/OPENCODE_ var reached this test.

    Requested by a handful of adapters' own "config defaults" tests to prove
    ``isolated_adapter_config_env`` above is actually doing its job, rather
    than each repeating the prefix tuple and the check.
    """
    leaked = [k for k in os.environ if k.startswith(_ADAPTER_CONFIG_ENV_PREFIXES)]
    assert leaked == [], f"leaked adapter-config env vars: {leaked}"


# =============================================================================
# Controllable on_execute handler (interrupt/stop tests)
# =============================================================================


class BlockingHandler:
    """Deterministic ``on_execute`` stand-in for interrupt/stop tests.

    Replaces the started/cancelled-event + hang-until-cancelled pattern that
    interrupt/stop tests otherwise hand-roll. On each cycle it records the
    message id, sets ``started``, optionally hangs until cancelled so a control
    signal can land mid-cycle, and sets ``cancelled`` if the cycle is aborted.

    ``block`` controls the hang:
    - ``True``  — every cycle hangs until cancelled.
    - ``False`` — no cycle hangs; each completes immediately.
    - ``int N`` — only the first ``N`` cycles hang (later ones complete), for
      the stop-then-replay case where the first attempt is aborted and the
      redelivered message must run to completion.

    ``invoked`` lists the message ids every cycle *entered*; ``completed``
    lists only those that ran to completion (never populated for an aborted
    hanging cycle).
    """

    def __init__(self, *, block: bool | int = True, block_seconds: float = 60) -> None:
        self.block = block
        self.block_seconds = block_seconds
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.invoked: list[str] = []
        self.completed: list[str] = []
        self.invocations = 0

    def _should_block(self) -> bool:
        if isinstance(self.block, bool):
            return self.block
        return self.invocations <= self.block

    async def __call__(self, ctx, event) -> None:
        self.invocations += 1
        msg_id = getattr(getattr(event, "payload", None), "id", None)
        if msg_id is not None:
            self.invoked.append(msg_id)
        self.started.set()
        try:
            if self._should_block():
                await asyncio.sleep(self.block_seconds)
            if msg_id is not None:
                self.completed.append(msg_id)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


# =============================================================================
# Event Factory Helpers (must return SDK-native types for pattern matching)
# =============================================================================


def make_message_event(
    room_id: str = "room-123",
    msg_id: str = "msg-123",
    content: str = "Test message",
    sender_id: str = "user-456",
    sender_type: str = "User",
    **kwargs,
) -> MessageEvent:
    """Create a MessageEvent using SDK-native types."""
    payload = MessageCreatedPayload(
        id=msg_id,
        content=content,
        message_type=kwargs.get("message_type", "text"),
        sender_id=sender_id,
        sender_type=sender_type,
        chat_room_id=room_id,
        inserted_at=kwargs.get("inserted_at", "2024-01-01T00:00:00Z"),
        updated_at=kwargs.get("updated_at", "2024-01-01T00:00:00Z"),
        metadata=kwargs.get("metadata", MessageMetadata(mentions=[])),
    )
    return MessageEvent(room_id=room_id, payload=payload)


def make_room_added_event(
    room_id: str = "room-123", title: str = "Test Room", **kwargs
) -> RoomAddedEvent:
    """Create a RoomAddedEvent using SDK-native types."""
    payload = RoomAddedPayload(
        id=room_id,
        title=title,
        task_id=kwargs.get("task_id"),
        inserted_at=kwargs.get("inserted_at", "2024-01-01T00:00:00Z"),
        updated_at=kwargs.get("updated_at", "2024-01-01T00:00:00Z"),
    )
    return RoomAddedEvent(room_id=room_id, payload=payload)


def make_room_removed_event(
    room_id: str = "room-123", title: str = "Test Room", **kwargs
) -> RoomRemovedEvent:
    """Create a RoomRemovedEvent using SDK-native types."""
    payload = RoomRemovedPayload(
        id=room_id,
        status=kwargs.get("status", "removed"),
        type=kwargs.get("type", "direct"),
        title=title,
        removed_at=kwargs.get("removed_at", "2024-01-01T00:00:00Z"),
    )
    return RoomRemovedEvent(room_id=room_id, payload=payload)


def make_room_deleted_event(room_id: str = "room-123") -> RoomDeletedEvent:
    """Create a RoomDeletedEvent using SDK-native types."""
    payload = RoomDeletedPayload(id=room_id)
    return RoomDeletedEvent(room_id=room_id, payload=payload)


def make_participant_mock(
    participant_id: str,
    name: str,
    type: str,
    handle: str | None = None,
    description: str | None = None,
) -> MagicMock:
    """A mock REST participant/peer model: attribute access + ``model_dump()``.

    One field list drives both access styles, so the mock cannot drift into a
    shape a real Fern model never has. ``mock.name`` must be assigned after
    construction — passing ``name=`` to ``MagicMock()`` sets the mock's
    identity, not its ``.name`` attribute.
    """
    fields = {
        "id": participant_id,
        "name": name,
        "type": type,
        "handle": handle,
        "description": description,
    }
    mock = MagicMock(**{k: v for k, v in fields.items() if k != "name"})
    mock.name = name
    mock.model_dump.return_value = dict(fields)
    return mock


@cache
def agent_api_namespace_classes() -> dict[str, type]:
    """The Fern client's agent-side namespace classes, keyed by attribute name.

    The namespaces are lazy properties, so the classes are harvested from a
    throwaway client instance (constructing one performs no I/O).
    """
    harvest = AsyncRestClient(api_key="spec-harvest", base_url="http://localhost:0")
    return {
        name: type(getattr(harvest, name))
        for name in dir(type(harvest))
        if name.startswith("agent_api_")
    }


@pytest.fixture
def mock_rest_client() -> MagicMock:
    """Mock AsyncRestClient shared by AgentTools, ContactTools and the ACP
    server adapter tests — the three suites that instantiate a REST-backed
    client directly.

    Spec'd against the real Fern client: every ``agent_api_*`` namespace is an
    autospec of its real class, so async methods are awaitable AsyncMocks with
    the real call signatures, and a ``band-client-rest`` bump that renames a
    namespace, drops a method, or changes a signature fails these tests
    instead of passing silently (the pin-bump tripwire the workarounds policy
    relies on).

    A test overrides whichever method's return value it needs; the ids below
    (``room-new-123``, ``msg-123``, ``evt-123``, ``user-1``, ``agent-2``) are
    asserted on directly by existing tests, so they are fixed rather than
    incidental.

    ``list_agent_chat_participants`` defaults to one non-self participant
    (``user-1``) rather than an empty list, since AgentTools/ContactTools
    tests assert mentions are generated from it. A test asserting exact
    message content after a call that mentions participants (e.g. ACP's
    ``handle_prompt``) must override ``list_agent_chat_participants`` to
    ``data=[]`` itself rather than relying on an empty default.
    """
    client = MagicMock(spec=AsyncRestClient)
    for name, namespace_class in agent_api_namespace_classes().items():
        # spec_set so overriding a method the real class no longer has fails
        # at the assignment, not as a dead attribute nothing ever calls.
        setattr(
            client, name, create_autospec(namespace_class, instance=True, spec_set=True)
        )

    # Chat creation (ACP: new/fork session). Each call gets its own room id
    # (first call is the fixed "room-new-123" existing tests assert on) so a
    # test creating two sessions (e.g. a fork) doesn't collide them onto one
    # room.
    room_ids = (f"room-new-{n}" for n in count(123))

    def _create_agent_chat_response(*_args: Any, **_kwargs: Any) -> MagicMock:
        response = MagicMock()
        response.data = MagicMock()
        response.data.id = next(room_ids)
        return response

    client.agent_api_chats.create_agent_chat.side_effect = _create_agent_chat_response

    # Message creation (AgentTools.send_message / ACP prompt forwarding)
    message_response = MagicMock()
    message_response.data = MagicMock()
    message_response.data.model_dump.return_value = {
        "id": "msg-123",
        "content": "Hello",
        "sender_id": "agent-1",
    }
    client.agent_api_messages.create_agent_chat_message.return_value = message_response

    # Event creation (AgentTools.send_event / ACP prompt forwarding)
    event_response = MagicMock()
    event_response.data = MagicMock()
    event_response.data.model_dump.return_value = {
        "id": "evt-123",
        "content": "Thinking...",
        "message_type": "thought",
    }
    client.agent_api_events.create_agent_chat_event.return_value = event_response

    # Participant listing (AgentTools.get_participants / ACP session bootstrap)
    participant1 = make_participant_mock(
        "user-1", "User One", "User", handle="user-one"
    )
    client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
        data=[participant1]
    )

    # Peer lookup (AgentTools.lookup_peers)
    peer1 = make_participant_mock(
        "agent-2", "Agent Two", "Agent", handle="agent-two", description="Another agent"
    )
    peers_response = MagicMock()
    peers_response.data = [peer1]
    peers_response.metadata = MagicMock()
    peers_response.metadata.page = 1
    peers_response.metadata.page_size = 50
    peers_response.metadata.total_count = 1
    peers_response.metadata.total_pages = 1
    peers_response.model_dump = MagicMock(
        return_value={
            "data": [
                {
                    "id": "agent-2",
                    "name": "Agent Two",
                    "type": "Agent",
                    "description": "Another agent",
                }
            ],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 1,
                "total_pages": 1,
            },
        }
    )
    client.agent_api_peers.list_agent_peers.return_value = peers_response

    return client


def make_participant_added_event(
    room_id: str = "room-123",
    participant_id: str = "user-456",
    name: str = "Test User",
    type: str = "User",
    **kwargs,
) -> ParticipantAddedEvent:
    """Create a ParticipantAddedEvent using SDK-native types."""
    payload = ParticipantAddedPayload(id=participant_id, name=name, type=type, **kwargs)
    return ParticipantAddedEvent(room_id=room_id, payload=payload)


def make_participant_removed_event(
    room_id: str = "room-123",
    participant_id: str = "user-456",
) -> ParticipantRemovedEvent:
    """Create a ParticipantRemovedEvent using SDK-native types."""
    payload = ParticipantRemovedPayload(id=participant_id)
    return ParticipantRemovedEvent(room_id=room_id, payload=payload)


def make_contact_request_received_event(
    id: str = "req-123",
    from_handle: str = "john_doe",
    from_name: str = "John Doe",
    **kwargs,
) -> ContactRequestReceivedEvent:
    """Create ContactRequestReceivedEvent for tests."""
    payload = ContactRequestReceivedPayload(
        id=id,
        from_handle=from_handle,
        from_name=from_name,
        message=kwargs.get("message"),
        status=kwargs.get("status", "pending"),
        inserted_at=kwargs.get("inserted_at", "2026-01-01T00:00:00Z"),
    )
    return ContactRequestReceivedEvent(payload=payload)


def make_contact_request_updated_event(
    id: str = "req-123",
    status: str = "approved",
) -> ContactRequestUpdatedEvent:
    """Create ContactRequestUpdatedEvent for tests."""
    payload = ContactRequestUpdatedPayload(
        id=id,
        status=status,
    )
    return ContactRequestUpdatedEvent(payload=payload)


def make_contact_added_event(
    contact_id: str = "contact-123",
    handle: str = "jane_smith",
    name: str = "Jane Smith",
    contact_type: str = "User",
    **kwargs,
) -> ContactAddedEvent:
    """Create ContactAddedEvent for tests."""
    payload = ContactAddedPayload(
        id=contact_id,
        handle=handle,
        name=name,
        type=contact_type,
        description=kwargs.get("description"),
        is_remote=kwargs.get("is_remote"),
        is_external=kwargs.get("is_external"),
        inserted_at=kwargs.get("inserted_at", "2026-01-01T00:00:00Z"),
    )
    return ContactAddedEvent(payload=payload)


def make_contact_removed_event(
    contact_id: str = "contact-123",
) -> ContactRemovedEvent:
    """Create ContactRemovedEvent for tests."""
    payload = ContactRemovedPayload(id=contact_id)
    return ContactRemovedEvent(payload=payload)


# =============================================================================
# SDK-Specific Fixtures
# =============================================================================


@pytest.fixture
def dummy_message_handler():
    """Dummy message handler for tests that don't need handler logic."""

    async def handler(msg: MessageCreatedPayload) -> None:
        pass

    return handler


@pytest.fixture
def mock_band_agent(mock_api_client, mock_websocket):
    """Mock BandAgent coordinator for session/adapter tests."""
    agent = AsyncMock()
    agent.agent_id = "agent-123"
    agent.agent_name = "TestBot"
    agent._api_client = mock_api_client
    agent._ws_client = mock_websocket
    agent.active_sessions = {}

    agent._send_message_internal = AsyncMock(
        return_value={"id": "msg-123", "status": "sent"}
    )
    agent._send_event_internal = AsyncMock(
        return_value={"id": "evt-123", "status": "sent"}
    )
    agent._add_participant_internal = AsyncMock(
        return_value={"id": "user-456", "name": "Test User", "role": "member"}
    )
    agent._remove_participant_internal = AsyncMock(
        return_value={"id": "user-456", "name": "Test User", "status": "removed"}
    )
    agent._lookup_peers_internal = AsyncMock(
        return_value={
            "peers": [{"id": "peer-1", "name": "Peer One", "type": "Agent"}],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "total_count": 1,
                "total_pages": 1,
            },
        }
    )
    agent._get_participants_internal = AsyncMock(
        return_value=[{"id": "agent-123", "name": "TestBot", "type": "Agent"}]
    )
    agent._create_chatroom_internal = AsyncMock(return_value="new-room-123")
    agent.get_context = AsyncMock()

    return agent


@pytest.fixture
def mock_agent_session():
    """Mock AgentSession for isolated tests."""
    session = AsyncMock()
    session.room_id = "room-123"
    session.is_llm_initialized = False
    session.participants = []
    session._last_participants_hash = None
    return session


@pytest.fixture
def sample_platform_message():
    """PlatformMessage fixture for new architecture."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="@TestBot hello",
        sender_id="user-456",
        sender_type="User",
        sender_name="Test User",
        message_type="text",
        metadata={"mentions": [{"id": "agent-123", "name": "TestBot"}]},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_agent_platform_message():
    """PlatformMessage from the agent itself (for filtering tests)."""
    return PlatformMessage(
        id="msg-456",
        room_id="room-123",
        content="Hello there!",
        sender_id="agent-123",
        sender_type="Agent",
        sender_name="TestBot",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
