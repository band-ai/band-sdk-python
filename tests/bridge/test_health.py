"""Tests for the bridge health endpoint (multi-agent aware)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from bridge_core.bridge import AgentRunner
from bridge_core.config import ReconnectConfig
from bridge_core.health import HealthServer

from .conftest import FakeForwarder, make_http_agent, make_link_mock


def _make_runner(agent_id: str, *, connected: bool = True) -> AgentRunner:
    link = make_link_mock()
    link.is_connected = connected
    return AgentRunner(
        agent_config=make_http_agent(agent_id=agent_id),
        ws_url="wss://test",
        rest_url="https://test",
        forwarder=FakeForwarder(),
        reconnect=ReconnectConfig(),
        shutdown_event=asyncio.Event(),
        link=link,
    )


async def test_health_healthy_when_all_connected() -> None:
    runners = [_make_runner("a1"), _make_runner("a2")]
    server = HealthServer(runners=runners, port=0)

    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "healthy"
        assert data["agent_count"] == 2
        ids = {a["agent_id"] for a in data["agents"]}
        assert ids == {"a1", "a2"}
        assert all(a["connected"] for a in data["agents"])


async def test_health_unhealthy_when_any_disconnected() -> None:
    runners = [
        _make_runner("a1", connected=True),
        _make_runner("a2", connected=False),
    ]
    server = HealthServer(runners=runners, port=0)

    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/health")
        assert resp.status == 503
        data = await resp.json()
        assert data["status"] == "unhealthy"

        per_agent = {a["agent_id"]: a["connected"] for a in data["agents"]}
        assert per_agent == {"a1": True, "a2": False}


async def test_health_warns_when_no_agents() -> None:
    server = HealthServer(runners=[], port=0)

    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/health")
        # No agents — vacuously healthy (200) but warn.
        assert resp.status == 200
        data = await resp.json()
        assert data["warning"] == "no agents configured"
        assert data["agent_count"] == 0


async def test_stop_handles_cleanup_error() -> None:
    server = HealthServer(runners=[], port=0)
    server._runner = MagicMock()
    server._runner.cleanup = AsyncMock(side_effect=OSError("cleanup boom"))

    await server.stop()  # Should not raise
    assert server._runner is None
