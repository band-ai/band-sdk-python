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

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

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

# Enable the `pytester` fixture (must live in the root conftest) so hook/plugin behaviour
# can be exercised in a real sub-run — used by tests/e2e/baseline/guards/test_agent_wiring.py.
pytest_plugins = ["pytester"]


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


def make_participant_added_event(
    room_id: str = "room-123",
    participant_id: str = "user-456",
    name: str = "Test User",
    type: str = "User",
    **kwargs,
) -> ParticipantAddedEvent:
    """Create a ParticipantAddedEvent using SDK-native types."""
    payload = ParticipantAddedPayload(
        id=participant_id,
        name=name,
        type=type,
        is_remote=kwargs.get("is_remote"),
        is_external=kwargs.get("is_external"),
    )
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
