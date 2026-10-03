"""Fake Band MCP backends for tests that never dial a server, and the one
seam (``create_band_mcp_backend``) every owner starts them through."""

from __future__ import annotations

import asyncio
import inspect
import itertools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from band.integrations.mcp import (
    BandMCPBackendSettings,
    BandMCPTransport,
    SharedBandMCPBackend,
)
from band.integrations.mcp.local_server import LOCAL_MCP_HOST
from tests.mcpclient import endpoint_path


@dataclass
class FakeLocalServer:
    port: int


class FakeBandMCPBackend:
    """A ``BandMCPBackend`` stand-in for tests that never dial it.

    Each fake gets its own port, so a replaced backend shows up as a changed
    URL. ``stop`` can be held open with ``stop_release`` to exercise a slow
    shutdown.
    """

    _ports = itertools.count(50000)

    def __init__(
        self,
        *,
        stop_started: asyncio.Event | None = None,
        stop_release: asyncio.Event | None = None,
    ) -> None:
        self.allowed_tools: list[str] = []
        self.local_server = FakeLocalServer(port=next(self._ports))
        self.is_running = True
        self.stop_calls = 0
        self._stop_started = stop_started
        self._stop_release = stop_release

    def endpoint(self, transport: BandMCPTransport, room_id: str | None = None) -> str:
        path = endpoint_path(transport, room_id)
        return f"http://{LOCAL_MCP_HOST}:{self.local_server.port}{path}"

    async def stop(self) -> None:
        self.stop_calls += 1
        self.is_running = False
        if self._stop_started is not None:
            self._stop_started.set()
        if self._stop_release is not None:
            await self._stop_release.wait()


class BackendStarts:
    """Stands in for ``create_band_mcp_backend``, recording the settings and
    the avoided port each start asked for.

    Starts are answered from ``outcomes`` in order -- a backend is returned,
    an exception raised -- and then by ``then`` (sync or async), or fail when
    ``then`` is None.
    """

    def __init__(self, outcomes: Sequence[Any], then: Callable[[], Any] | None) -> None:
        self._outcomes = list(outcomes)
        self._then = then
        self.requested: list[BandMCPBackendSettings] = []
        self.avoided: list[int | None] = []

    async def __call__(
        self, settings: BandMCPBackendSettings, *, avoid_port: int | None = None
    ) -> Any:
        self.requested.append(settings)
        self.avoided.append(avoid_port)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if self._then is None:
            raise AssertionError("unexpected Band MCP backend start")
        backend = self._then()
        return await backend if inspect.isawaitable(backend) else backend


@contextmanager
def backends_created_by(
    *outcomes: Any, then: Callable[[], Any] | None = FakeBandMCPBackend
) -> Iterator[BackendStarts]:
    """Every Band MCP backend start, faked at the one seam all owners use."""
    starts = BackendStarts(outcomes, then)
    with patch("band.integrations.mcp.backends.create_band_mcp_backend", starts):
        yield starts


async def hold_backend(owner: SharedBandMCPBackend, backend: Any = None) -> Any:
    """Have ``owner`` hold ``backend`` (a fresh fake by default) as though it
    had started it."""
    backend = backend or FakeBandMCPBackend()
    with backends_created_by(backend):
        await owner.ensure()
    return backend
