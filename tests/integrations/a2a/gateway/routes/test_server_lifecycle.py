"""Gateway server lifecycle behavior."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from band.integrations.a2a.gateway.server import SERVER_STOP_TIMEOUT_S, GatewayServer
from tests.integrations.a2a.gateway.helpers import make_peer
from tests.integrations.a2a.gateway.serverkit import (
    DelayedTwoStepExecutor,
    NeverFinishingExecutor,
    build_server,
    hello_message_body,
)
from tests.lifecycle import elapsed, held_open, running


class TestServerLifecycle:
    async def test_start_returns_only_once_the_server_is_listening(
        self,
    ) -> None:
        """A caller dialing in right after ``on_started()`` returns (e.g. a real
        A2A client, or one of the E2E smokes) must not race a socket that isn't
        accepting connections yet."""
        async with running(build_server(port=0)) as server:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://127.0.0.1:{server.bound_port}/peers"
                )
            assert response.status_code == 200

    async def test_a_second_server_is_not_poisoned_by_a_prior_servers_shutdown(
        self,
    ) -> None:
        """Regression: a second GatewayServer's live stream, opened only after a
        first one has stopped in the same process, must still deliver every
        event."""
        async with running(build_server(port=0)) as first:
            port1 = first.bound_port
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST",
                    f"http://127.0.0.1:{port1}/agents/weather-agent/message:stream",
                    headers={"A2A-Version": "1.0"},
                    json=hello_message_body(),
                ) as response,
            ):
                async for _ in response.aiter_bytes():
                    pass

        second = GatewayServer(
            peers={"other-agent": make_peer("uuid-other", "Other Agent", "")},
            gateway_url="http://localhost:0",
            port=0,
            executor_factory=lambda _slug: DelayedTwoStepExecutor(),
        )
        async with running(second):
            port2 = second.bound_port
            events: list[str] = []
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST",
                    f"http://127.0.0.1:{port2}/agents/other-agent/message:stream",
                    headers={"A2A-Version": "1.0"},
                    json=hello_message_body(),
                ) as response,
            ):
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events.append(line)

            assert len(events) >= 3, (
                f"got {len(events)} events, expected 3 (task, working, completed) -- "
                "the second server's stream was cut short by the first server's shutdown"
            )

    @pytest.mark.timeout(SERVER_STOP_TIMEOUT_S + 15.0)
    async def test_stop_returns_promptly_with_a_still_open_message_stream(self) -> None:
        """Regression: disabling sse_starlette's drain means a live
        message:stream connection has no other way to end -- stop() must bound
        its wait via timeout_graceful_shutdown instead of hanging forever.

        Measures wall-clock time around a bare ``server.stop()`` (wrapping it
        in ``asyncio.wait_for`` would cancel it externally and mask a real hang).
        """
        executor = NeverFinishingExecutor()
        server = build_server(port=0, executor_factory=lambda _slug: executor)
        async with running(server):
            port = server.bound_port

            async def connect(ready: asyncio.Event) -> None:
                try:
                    # timeout=None: httpx's default 5s read timeout would otherwise
                    # give up waiting for the next chunk and disconnect on its own
                    # around the same mark as SERVER_STOP_TIMEOUT_S -- masking a
                    # real server-side hang as a false pass, since the connection
                    # would end for the wrong reason (a bored client) rather than
                    # proving stop() itself is bounded.
                    async with (
                        httpx.AsyncClient(timeout=None) as client,
                        client.stream(
                            "POST",
                            f"http://127.0.0.1:{port}/agents/weather-agent/message:stream",
                            headers={"A2A-Version": "1.0"},
                            json=hello_message_body(),
                        ) as response,
                    ):
                        response.raise_for_status()
                        async for _ in response.aiter_bytes():
                            ready.set()
                except (httpx.ReadError, httpx.RemoteProtocolError):
                    if not ready.is_set():
                        raise

            async with held_open(connect):
                stop_elapsed = await elapsed(server.stop())

            assert stop_elapsed < SERVER_STOP_TIMEOUT_S + 5.0, (
                f"stop() took {stop_elapsed:.1f}s -- graceful shutdown is not "
                "bounded by SERVER_STOP_TIMEOUT_S"
            )
            assert executor.finished.is_set()
