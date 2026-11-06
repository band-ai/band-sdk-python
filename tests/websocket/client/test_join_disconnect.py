"""Startup rollback waits for remote close decisions without blocking joins."""

from __future__ import annotations

import asyncio

import pytest
from phoenix_channels_python_client import SocketError
from websockets.frames import CloseCode

from band.client.streaming import WebSocketClient
from band.client.streaming.disconnect import WebSocketDisconnectReason
from band.testing import FakePhoenixServer, fake_phoenix_server
from tests.conftest import CLOSED_1008
from tests.identifiers import AGENT_ID
from tests.platform.linkclose.kit import CONTROL_TOPIC
from tests.sessionpolicies import session_policy
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.websocket.client.disconnect import DisconnectGate, reject_supersede


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestJoinDisconnect:
    async def test_an_open_socket_needs_no_disconnect_decision(
        self, disconnect_client: tuple[FakePhoenixServer, WebSocketClient]
    ) -> None:
        _, client = disconnect_client
        async with client:
            await client.wait_for_disconnect_decision()
            await client.join_agent_control_channel(AGENT_ID, reject_supersede)
            assert client.joined_topics() == frozenset({CONTROL_TOPIC})

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            (CloseCode.NORMAL_CLOSURE, None),
            (CloseCode.GOING_AWAY, None),
            (CloseCode.POLICY_VIOLATION, CLOSED_1008),
        ],
    )
    async def test_startup_rollback_waits_for_the_remote_close_decision(
        self,
        code: CloseCode,
        reason: WebSocketDisconnectReason | None,
        disconnect_gate: DisconnectGate,
        disconnect_client: tuple[FakePhoenixServer, WebSocketClient],
    ) -> None:
        server, client = disconnect_client
        async with client:
            with server.defer_join(CONTROL_TOPIC) as deferred:
                async with background(
                    client.join_agent_control_channel(AGENT_ID, reject_supersede)
                ) as joining:
                    await deferred.ready.wait()
                    await server.close_connection(code=code)
                    await disconnect_gate.ready.wait()
                    with pytest.raises(SocketError):
                        await joining
                    async with background(
                        disconnect_gate.wait_for_decision(client)
                    ) as deciding:
                        await disconnect_gate.waiting.wait()
                        assert not deciding.done()
                        disconnect_gate.released.set()
                        await deciding

            assert client.last_disconnect_reason == reason

    async def test_local_end_releases_startup_rollback_waiting_for_the_close_decision(
        self,
        disconnect_gate: DisconnectGate,
        disconnect_client: tuple[FakePhoenixServer, WebSocketClient],
    ) -> None:
        server, client = disconnect_client
        async with client:
            with server.defer_join(CONTROL_TOPIC) as deferred:
                async with background(
                    client.join_agent_control_channel(AGENT_ID, reject_supersede)
                ) as joining:
                    await deferred.ready.wait()
                    await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                    await disconnect_gate.ready.wait()
                    with pytest.raises(SocketError):
                        await joining
                    async with background(
                        disconnect_gate.wait_for_decision(client)
                    ) as deciding:
                        await disconnect_gate.waiting.wait()
                        client.end_session()
                        await deciding
                    disconnect_gate.released.set()

            assert client.last_disconnect_reason is None

    async def test_cancelling_the_decision_wait_allows_confirmed_client_cleanup(
        self,
        disconnect_gate: DisconnectGate,
        disconnect_client: tuple[FakePhoenixServer, WebSocketClient],
    ) -> None:
        server, client = disconnect_client
        async with client:
            with server.defer_join(CONTROL_TOPIC) as deferred:
                async with background(
                    client.join_agent_control_channel(AGENT_ID, reject_supersede)
                ) as joining:
                    await deferred.ready.wait()
                    await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                    await disconnect_gate.ready.wait()
                    with pytest.raises(SocketError):
                        await joining
                    async with background(
                        disconnect_gate.wait_for_decision(client)
                    ) as deciding:
                        await disconnect_gate.waiting.wait()
                        deciding.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await deciding

        assert client.joined_topics() == frozenset()
        assert client.last_disconnect_reason is None
        async with client:
            await client.join_agent_control_channel(AGENT_ID, reject_supersede)
            assert client.joined_topics() == frozenset({CONTROL_TOPIC})

    async def test_reconnect_callback_join_failure_does_not_wait_for_its_supervisor(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:

            async def recover_control() -> None:
                with pytest.raises(SocketError):
                    await client.join_agent_control_channel(AGENT_ID, reject_supersede)

            client = WebSocketClient(
                server.url,
                "test-key",
                AGENT_ID,
                on_reconnect=recover_control,
                session_policy=session_policy(),
            )
            async with client:
                with server.defer_join(CONTROL_TOPIC) as deferred:
                    await server.close_connection(code=CloseCode.GOING_AWAY)
                    await deferred.ready.wait()
                    await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                    with pytest.raises(SocketError, match="1008"):
                        await client.run_forever(install_signal_handlers=False)

                assert client.last_disconnect_reason == CLOSED_1008
