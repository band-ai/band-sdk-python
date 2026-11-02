"""A ``WebSocketClient`` entered again after a terminal supersede starts a new
lifecycle: its transport serves the host rather than inheriting the shutdown
that ended the previous one."""

from __future__ import annotations

import asyncio

from band.client.streaming import (
    AgentControlPayload,
    WebSocketClient,
    WebSocketDisconnectReason,
)
from band.client.streaming.client import WireEvent
from band.testing import fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    AGENT_ID,
    CONTROL_TOPIC,
    HANG_GUARD_S,
    SUPERSEDED,
    control_wire_payload,
)


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
                run = asyncio.create_task(ws.run_forever(install_signal_handlers=False))
                await server.push(
                    CONTROL_TOPIC,
                    WireEvent.AGENT_CONTROL,
                    control_wire_payload("control-0"),
                )
                await asyncio.wait_for(controlled.wait(), HANG_GUARD_S)

                assert not run.done()
                assert ws.last_disconnect_reason is None
                assert verdicts == [SUPERSEDED]

                run.cancel()
                await asyncio.wait({run})
