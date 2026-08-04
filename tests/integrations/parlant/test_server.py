"""Behavioral tests for the in-process Parlant server lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import band.integrations.parlant.server as server_module
from band.integrations.parlant.server import running_parlant_server


class StubServer:
    """Model Parlant's configure, setup, serve, and cleanup phases."""

    instances: list[StubServer] = []

    def __init__(self, **_kwargs) -> None:
        self.events: list[str] = []
        self.ready = asyncio.Event()
        self._exit_stack = AsyncMock()
        self._startup_context_manager = AsyncMock()
        self.instances.append(self)

    async def __aenter__(self):
        self.events.append("enter")
        return self

    async def __aexit__(self, exc_type, exc_value, _tb):
        if exc_value is not None:
            self.events.append("abort")
            await self._exit_stack.aclose()
            return False

        self.events.extend(["evaluations", "retrievers", "serve"])
        self.ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.events.append("cleanup")
            await self._exit_stack.aclose()
        return False


@pytest.fixture(autouse=True)
def stub_server(monkeypatch):
    StubServer.instances.clear()
    parlant_sdk = pytest.importorskip("parlant.sdk")
    monkeypatch.setattr(parlant_sdk, "Server", StubServer)


async def test_yields_only_after_setup_and_stops_server_before_returning():
    async def setup(server):
        server.events.append("configure")

    async with running_parlant_server(setup=setup) as server:
        assert server.events == [
            "enter",
            "configure",
            "evaluations",
            "retrievers",
            "serve",
        ]

    assert server.events[-1] == "cleanup"
    server._exit_stack.aclose.assert_awaited_once()


async def test_configuration_failure_uses_parlant_exception_cleanup():
    async def setup(_server):
        raise RuntimeError("configuration failed")

    with pytest.raises(RuntimeError, match="configuration failed"):
        async with running_parlant_server(setup=setup):
            pass

    server = StubServer.instances[-1]
    assert server.events == ["enter", "abort"]
    server._exit_stack.aclose.assert_awaited_once()


async def test_enter_failure_closes_server_owned_resources(monkeypatch):
    class FailingServer(StubServer):
        async def __aenter__(self):
            self.events.append("enter")
            raise RuntimeError("initialize failed")

    monkeypatch.setattr(server_module.p, "Server", FailingServer)

    with pytest.raises(RuntimeError, match="initialize failed"):
        async with running_parlant_server():
            pass

    FailingServer.instances[-1]._exit_stack.aclose.assert_awaited_once()


async def test_startup_system_exit_is_catchable_and_resources_close(monkeypatch):
    class DyingServer(StubServer):
        async def __aenter__(self):
            raise SystemExit(1)

    monkeypatch.setattr(server_module.p, "Server", DyingServer)

    with pytest.raises(ValueError, match="failed to start"):
        async with running_parlant_server():
            pass

    DyingServer.instances[-1]._exit_stack.aclose.assert_awaited_once()


async def test_pre_ready_setup_failure_closes_both_resource_stacks(monkeypatch):
    class EvaluationFailureServer(StubServer):
        async def __aexit__(self, exc_type, exc_value, _tb):
            if exc_value is not None:
                return await super().__aexit__(exc_type, exc_value, _tb)
            self.events.append("evaluations")
            raise RuntimeError("evaluation failed")

    monkeypatch.setattr(server_module.p, "Server", EvaluationFailureServer)

    with pytest.raises(RuntimeError, match="evaluation failed"):
        async with running_parlant_server():
            pass

    server = EvaluationFailureServer.instances[-1]
    server._startup_context_manager.__aexit__.assert_awaited_once()
    server._exit_stack.aclose.assert_awaited_once()
