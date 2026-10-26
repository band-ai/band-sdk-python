"""Hold MCP resolution through cancellation and settlement."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
)
from band.integrations.ompprocess import OmpMcpServer, OmpMcpTransport
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.turns import run_turn


class CleanupObservedAdapter(OmpAdapter):
    """Expose cleanup entry without replacing a running adapter's method."""

    def __init__(self, config: OmpAdapterConfig | None = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.cleanup_entered = asyncio.Event()

    async def on_cleanup(self, room_id: str) -> None:
        self.cleanup_entered.set()
        await super().on_cleanup(room_id)


def remote(token: str) -> OmpMcpServer:
    return OmpMcpServer(
        type=OmpMcpTransport.HTTP,
        url="http://127.0.0.1:9/mcp",
        headers={"Authorization": f"Bearer {token}"},
    )


class SettlingResolver:
    """Blocks the first resolution; when cancelled it settles durable work before
    re-raising, and counts every cancellation it receives along the way."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.settle = asyncio.Event()
        self.settled = False
        self.cancellations = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1  # settlement interrupted by a second cancel
                raise
            self.settled = True
            raise
        return {}


def settling_adapter(tmp_path: Path, resolver, **config):
    return CleanupObservedAdapter(
        OmpAdapterConfig(cwd=str(tmp_path), **config),
        resolve_mcp_servers=resolver,
        emit=(),
    )


async def start_blocked_turn(adapter, resolver: SettlingResolver):
    first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
    await asyncio.wait_for(resolver.entered.wait(), 1)
    return first


async def checkpoint() -> None:
    resumed = asyncio.Event()
    asyncio.get_running_loop().call_soon(resumed.set)
    await resumed.wait()


async def start_cleanup(
    adapter: CleanupObservedAdapter, *, shutdown: bool = False
) -> asyncio.Task[None]:
    adapter.cleanup_entered.clear()
    operation = adapter.cleanup_all() if shutdown else adapter.on_cleanup("room-123")
    task = asyncio.create_task(operation)
    await adapter.cleanup_entered.wait()
    return task


async def cross_deadline(clock, seconds: float) -> None:
    crossed = asyncio.Event()
    handle = asyncio.get_running_loop().call_later(seconds, crossed.set)
    try:
        clock.advance(seconds)
        await asyncio.wait_for(crossed.wait(), 1)
    finally:
        handle.cancel()


class SwallowingResolver:
    """Catches a cancellation at its first await and returns a server anyway."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _workspace: str):
        self.calls += 1
        try:
            await checkpoint()
        except asyncio.CancelledError:
            pass
        return {"linear": remote("late")}


class SelfRetiringResolver(SettlingResolver):
    """On its first cancellation it asks for its own room's cleanup, then holds
    its settlement until released."""

    adapter: OmpAdapter

    async def __call__(self, workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancellations += 1
            await self.adapter.on_cleanup("room-123")  # returns at once for self
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            raise
        return {}


class UncancellingResolver(SettlingResolver):
    """Consumes the deadline's cancellation with the public ``uncancel()``, then
    settles; ``returns`` decides whether it re-raises or returns a server."""

    def __init__(self, *, returns: bool) -> None:
        super().__init__()
        self.returns = returns

    async def __call__(self, _workspace: str):
        self.calls += 1
        if self.calls > 1:
            return {}
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            self.cancellations += 1
            self.cancel_seen.set()
            try:
                await self.settle.wait()
            except asyncio.CancelledError:
                self.cancellations += 1
                raise
            self.settled = True
            if not self.returns:
                raise
        return {"linear": remote("late")}


def timeout_reports(tools) -> list[tuple[str, str | None]]:
    return [(f["provider"], f.get("code")) for f in reported_failures(tools)]
