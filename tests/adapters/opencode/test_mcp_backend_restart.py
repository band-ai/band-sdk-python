"""A Band MCP server that dies between turns is restarted on a new port and
re-registered with OpenCode under the same name."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

import pytest

from band.adapters.opencode import OpencodeAdapter
from band.integrations.mcp import BandMCPTransport
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_session_idle,
    make_platform_message,
    tools_protocol,
    wait_for,
)
from tests.mcpclient import crash_server, mcp_session


@pytest.fixture(autouse=True)
def patch_mcp_backend() -> Iterator[None]:
    """Run the real Band MCP backend instead of the suite's fake."""
    yield


async def test_a_crashed_server_is_re_registered_on_its_new_port(
    make_adapter: Callable[..., OpencodeAdapter], tools: FakeAgentTools
) -> None:
    client = FakeOpencodeClient(
        prompt_event_sequences=[
            [event_session_idle("sess-1")],
            [event_session_idle("sess-1")],
        ]
    )
    adapter = make_adapter(client)
    await adapter.on_started("OpenCode Agent", "A coding agent")

    async def send(content: str, *, bootstrap: bool) -> None:
        await adapter.on_message(
            make_platform_message(content=content),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=bootstrap,
            room_id="room-1",
        )
        room = await adapter._get_or_create_room_state("room-1")
        await wait_for(lambda: room.turn is None or room.turn.turn_future.done())

    try:
        await send("before the crash", bootstrap=True)
        backend = adapter._mcp.current
        assert backend is not None
        await crash_server(backend.local_server)
        await send("after the crash", bootstrap=False)

        crashed, live = client.registered_mcp_servers
        async with mcp_session(live["url"], BandMCPTransport.SSE) as session:
            served = {tool.name for tool in (await session.list_tools()).tools}
    finally:
        await adapter.on_cleanup("room-1")

    assert live["name"] == crashed["name"]
    assert urlsplit(live["url"]).port != urlsplit(crashed["url"]).port
    assert BandTool.SEND_MESSAGE in served
