from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter


def running_server(rest: MagicMock) -> tuple[BandACPServerAdapter, ACPServer]:
    adapter = BandACPServerAdapter(rest_client=rest)
    adapter._session_to_room["session-1"] = "room-123"
    adapter._room_to_session["room-123"] = "session-1"
    adapter.set_acp_client(AsyncMock())
    return adapter, ACPServer(adapter)


def prompt(server: ACPServer) -> asyncio.Task[object]:
    return asyncio.create_task(
        server.prompt(session_id="session-1", prompt=[{"text": "Hello"}])
    )


def assert_default_room_failure(error: RequestError) -> None:
    assert error.code == -32603
    assert error.data == AgentFailure("band", "Peer failed").to_extension_data()
