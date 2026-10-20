"""A ``WebSocketClient`` entered again after a terminal supersede starts a new
lifecycle: its transport serves the host rather than inheriting the shutdown
that ended the previous one."""

from __future__ import annotations

import asyncio

from band.client.streaming import AgentControlPayload, WebSocketClient
from band.client.streaming.client import SupersedePayload, WireEvent
from band.testing import fake_phoenix_server
from tests.conftest import TERMINAL_SUPERSEDE
from tests.platform.linkclose.kit import (
    AGENT_ID,
    CONTROL_TOPIC,
    HANG_GUARD_S,
    control_wire_payload,
)


class TestReusedClient:
    async def test_run_forever_serves_the_connection_after_a_supersede(self) -> None:
        async with fake_phoenix_server() as server:
            ws = WebSocketClient(server.url, "test-key", AGENT_ID)

            async def end_on_supersede(payload: SupersedePayload) -> None:
                outcome = ws.handle_supersede(payload.retryable, payload.retry_after)
                ws.record_terminal_disconnect(payload.to_disconnect_reason(outcome))

            async with ws:
                await ws.join_agent_control_channel(
                    AGENT_ID, on_supersede=end_on_supersede
                )
                await server.push(
                    CONTROL_TOPIC, WireEvent.SUPERSEDE, TERMINAL_SUPERSEDE
                )
                await asyncio.wait_for(
                    ws.run_forever(install_signal_handlers=False), HANG_GUARD_S
                )

            controlled = asyncio.Event()

            async def on_control(payload: AgentControlPayload) -> None:
                controlled.set()

            async with ws:
                await ws.join_agent_control_channel(
                    AGENT_ID, on_supersede=end_on_supersede, on_control=on_control
                )
                run = asyncio.create_task(ws.run_forever(install_signal_handlers=False))
                await server.push(
                    CONTROL_TOPIC,
                    WireEvent.AGENT_CONTROL,
                    control_wire_payload("control-0"),
                )
                await asyncio.wait_for(controlled.wait(), HANG_GUARD_S)

                assert not run.done()

                run.cancel()
                await asyncio.wait({run})
