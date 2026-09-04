"""Behavior tests for the shared uvicorn startup wait.

``mcp.local_server`` and ``a2a.gateway.server`` each embed their own uvicorn
server and both wait on this one helper before reporting started -- these
tests live here, once, instead of being duplicated per caller.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from band.integrations.uvicorn_server import wait_until_started


class FakeUvicornServer:
    def __init__(self, *, started: bool = False) -> None:
        self.started = started


@pytest.mark.asyncio
async def test_returns_once_the_server_flips_ready() -> None:
    server = FakeUvicornServer()
    serve_task = asyncio.create_task(asyncio.sleep(10))

    async def flip_ready_soon() -> None:
        await asyncio.sleep(0.1)
        server.started = True

    flipper = asyncio.create_task(flip_ready_soon())
    try:
        await asyncio.wait_for(
            wait_until_started(server, serve_task, timeout_s=5.0), timeout=2.0
        )
    finally:
        for task in (serve_task, flipper):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_surfaces_a_serve_task_failure_immediately() -> None:
    """A serve task that dies before the server ever reports ready (e.g. a
    port already in use) must surface its real exception right away --
    busy-waiting the full timeout and raising a generic one instead would
    hide the actual cause."""

    async def fail_immediately() -> None:
        raise OSError("address already in use")

    server = FakeUvicornServer()
    serve_task = asyncio.create_task(fail_immediately())

    start = asyncio.get_running_loop().time()
    with pytest.raises(OSError, match="address already in use"):
        await wait_until_started(server, serve_task, timeout_s=30.0)

    assert asyncio.get_running_loop().time() - start < 1.0


@pytest.mark.asyncio
async def test_times_out_if_the_server_never_reports_ready() -> None:
    server = FakeUvicornServer()
    serve_task = asyncio.create_task(asyncio.sleep(10))
    try:
        with pytest.raises(TimeoutError):
            await wait_until_started(server, serve_task, timeout_s=0.2)
    finally:
        serve_task.cancel()
        with suppress(asyncio.CancelledError):
            await serve_task
