"""Shared connections, adapters and event projections for ACPClientAdapter tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from acp.schema import (
    NewSessionResponse,
)

from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.acp.client_runtime import ACPRuntime
from band.testing import FakeAgentTools
from tests.integrations.acp.acp_toolkit.harness import (
    FakeSpawn,
    inject_acp_spawn,
    make_acp_connection,
)

MOCK_ROOM = "room-123"
CODEX = ACPClientAdapterConfig(command="codex")


def advertise_session_load(spawn: FakeSpawn) -> None:
    """Make the scripted connection advertise ``loadSession`` at initialize."""
    spawn.conn.initialize.return_value.agent_capabilities.load_session = True


def permission_events(tools: FakeAgentTools) -> list[dict[str, object]]:
    """The permission tool_call/tool_result events the handler posted to the room."""
    return [
        event
        for event in tools.events_sent
        if (event.get("metadata") or {}).get("permission_request")
    ]


def event_types(events: list[dict[str, object]]) -> list[object]:
    """The ordered ``message_type`` of each event — for asserting a pair's shape."""
    return [event["message_type"] for event in events]


def metadata_values(events: list[dict[str, object]], key: str) -> list[object]:
    """The ordered value of one metadata field across a set of events."""
    return [event["metadata"][key] for event in events]


def scripted_conn(sessions: dict[str, str]) -> AsyncMock:
    """A connection whose ``new_session`` names each room's session from
    ``sessions``, keyed by the room id its workspace ends in."""
    conn = make_acp_connection()
    conn.new_session = AsyncMock(
        side_effect=lambda *, cwd, mcp_servers: NewSessionResponse(
            session_id=sessions[Path(cwd).name]
        )
    )
    return conn


def adapter_on(conn: AsyncMock, **settings: object) -> ACPClientAdapter:
    """An adapter whose room processes all run on ``conn``."""
    adapter = ACPClientAdapter(
        ACPClientAdapterConfig(command="codex", inject_band_tools=False, **settings)
    )
    inject_acp_spawn(adapter, FakeSpawn(conn=conn))
    return adapter


def room_runtime(adapter: ACPClientAdapter, room: str = MOCK_ROOM) -> ACPRuntime:
    """The process ``adapter`` currently runs for ``room``."""
    runtime = adapter._rooms[room].runtime
    assert runtime is not None
    return runtime
