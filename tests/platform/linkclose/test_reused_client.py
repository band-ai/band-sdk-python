"""A ``WebSocketClient`` entered again after a terminal verdict (a supersede, a
refused boot) starts a new lifecycle: its transport serves the host rather than
inheriting the verdict or shutdown that ended the previous one."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest

from band.client.streaming import (
    AgentControlPayload,
    WebSocketClient,
    WebSocketDisconnectReason,
    WebSocketUpgradeError,
)
from band.client.streaming.client import WireEvent
from band.testing import fake_phoenix_server
from tests.conftest import SUPERSEDED, TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    AGENT_ID,
    CONTROL_TOPIC,
    HANG_GUARD_S,
    control_wire_payload,
)
from tests.toolkit.tasks import background


class TestReusedClient:
    async def test_run_forever_serves_the_connection_after_a_supersede(self) -> None:
        async with fake_phoenix_server() as server:
            verdicts: list[WebSocketDisconnectReason] = []
            ws = WebSocketClient(
                server.url, "test-key", AGENT_ID, on_terminal=verdicts.append
            )

            async with ws:
                await ws.join_agent_control_channel(
                    AGENT_ID, on_supersede=ws.handle_supersede
                )
                await server.push(
                    CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE
                )
                await asyncio.wait_for(
                    ws.run_forever(install_signal_handlers=False), HANG_GUARD_S
                )
                assert verdicts == [SUPERSEDED]

            controlled = asyncio.Event()

            async def on_control(payload: AgentControlPayload) -> None:
                controlled.set()

            async with ws:
                await ws.join_agent_control_channel(
                    AGENT_ID, on_supersede=ws.handle_supersede, on_control=on_control
                )
                async with background(
                    ws.run_forever(install_signal_handlers=False)
                ) as run:
                    await server.push(
                        CONTROL_TOPIC,
                        WireEvent.AGENT_CONTROL,
                        control_wire_payload("control-0"),
                    )
                    await asyncio.wait_for(controlled.wait(), HANG_GUARD_S)

                    assert not run.done()
                    assert ws.last_disconnect_reason is None
                    assert verdicts == [SUPERSEDED]

    async def test_a_client_refused_at_boot_enters_a_fresh_session(self) -> None:
        async with fake_phoenix_server() as server:
            client = WebSocketClient(server.url, "test-key", AGENT_ID)
            with (
                server.refusing_upgrades(HTTPStatus.FORBIDDEN),
                pytest.raises(WebSocketUpgradeError),
            ):
                await client.__aenter__()
            async with client:
                assert client.last_disconnect_reason is None
            async with client:
                assert server.connection_count == 2
