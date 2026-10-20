"""Successful handler closes do not replay while unfinished peers stay owned."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest

from tests.integrations.a2a.gateway.serverkit import (
    build_multi_peer_server,
    build_server,
    hello_message_body,
)
from tests.lifecycle import running


class TestRetryOwnership:
    async def test_failed_handler_retries_without_replaying_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = build_multi_peer_server()
        server._build_app()
        completed: list[str] = []
        attempts: list[str] = []
        for slug, handler in server._handlers.items():
            original = handler.aclose

            async def close(
                name: str = slug, operation: Callable[[], Awaitable[None]] = original
            ) -> None:
                attempts.append(name)
                if name == "billing-agent" and attempts.count(name) == 1:
                    raise OSError("Temporary handler failure")
                await operation()
                completed.append(name)

            monkeypatch.setattr(handler, "aclose", close)
        with pytest.raises(ExceptionGroup, match="retry stop"):
            await server.stop()
        await server.stop()
        assert completed == ["weather-agent", "billing-agent"]
        assert attempts == ["weather-agent", "billing-agent", "billing-agent"]

    async def test_cancelled_handler_close_retains_ownership(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = build_server()
        server._build_app()
        handler = server._handlers["weather-agent"]
        original = handler.aclose
        entered = asyncio.Event()

        async def interrupted() -> None:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(handler, "aclose", interrupted)
        stopping = asyncio.create_task(server.stop())
        await entered.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        monkeypatch.setattr(handler, "aclose", original)
        await server.stop()
        assert not server._handlers

    async def test_restart_accepts_another_real_request(self) -> None:
        server = build_server(port=0)
        for run in range(2):
            async with running(server), httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://127.0.0.1:{server.bound_port}/agents/uuid-weather/message:send",
                    headers={"A2A-Version": "1.0"},
                    json=hello_message_body(f"message-{run}"),
                )
                response.raise_for_status()
                assert (
                    response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
                )
