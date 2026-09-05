"""Behavior tests for the shared embedded-uvicorn-server lifecycle.

``mcp.local_server``, ``a2a.gateway.server``, and the A2A baseline test
fixture each embed their own uvicorn server -- ``wait_until_started`` and
``ManagedUvicornServer`` live here, once, instead of every caller
re-deriving (and re-testing) the same startup/shutdown correctness.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sse_starlette.sse import AppStatus

from band.integrations.uvicorn_server import ManagedUvicornServer, wait_until_started

from tests.lifecycle import backgrounded, running


class FakeUvicornServer:
    def __init__(self, *, started: bool = False) -> None:
        self.started = started


async def _minimal_asgi_app(scope: dict, receive: object, send: object) -> None:
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.asyncio
async def test_returns_once_the_server_flips_ready() -> None:
    server = FakeUvicornServer()

    async def flip_ready_soon() -> None:
        await asyncio.sleep(0.1)
        server.started = True

    async with (
        backgrounded(asyncio.sleep(10)) as serve_task,
        backgrounded(flip_ready_soon()),
    ):
        await asyncio.wait_for(
            wait_until_started(server, serve_task, timeout_s=5.0), timeout=2.0
        )


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
async def test_surfaces_a_clean_task_completion_that_never_started() -> None:
    """A serve task that ends without ever setting ``started`` -- e.g. an
    early shutdown signal, not a raised exception -- must be treated as
    fatal immediately too; only distinguishing "done and raised" from "done"
    would still busy-wait the full timeout on this path."""
    server = FakeUvicornServer()
    serve_task = asyncio.create_task(asyncio.sleep(0))

    start = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match="ended before ever starting"):
        await wait_until_started(server, serve_task, timeout_s=30.0)

    assert asyncio.get_running_loop().time() - start < 1.0


@pytest.mark.asyncio
async def test_times_out_if_the_server_never_reports_ready() -> None:
    server = FakeUvicornServer()
    async with backgrounded(asyncio.sleep(10)) as serve_task:
        with pytest.raises(TimeoutError):
            await wait_until_started(server, serve_task, timeout_s=0.2)


@pytest.mark.asyncio
async def test_managed_server_starts_and_bound_port_resolves_real_port() -> None:
    server = ManagedUvicornServer(
        _minimal_asgi_app,
        host="127.0.0.1",
        port=0,
        start_timeout_s=5.0,
        stop_timeout_s=5,
    )
    async with running(server):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{server.bound_port}/")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_stop_before_start_is_a_no_op() -> None:
    server = ManagedUvicornServer(
        _minimal_asgi_app,
        host="127.0.0.1",
        port=0,
        start_timeout_s=5.0,
        stop_timeout_s=5,
    )
    await server.stop()  # must not raise
    with pytest.raises(RuntimeError, match="has not started"):
        _ = server.bound_port


@pytest.mark.asyncio
async def test_start_cleans_up_when_the_startup_wait_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed/timed-out startup wait must still tell uvicorn to exit and
    clear server state, not leave a listening socket and stray task behind."""
    import band.integrations.uvicorn_server as uvicorn_server_module

    async def failing_wait_until_started(*args: object, **kwargs: object) -> None:
        raise TimeoutError("simulated startup failure")

    monkeypatch.setattr(
        uvicorn_server_module, "wait_until_started", failing_wait_until_started
    )

    server = ManagedUvicornServer(
        _minimal_asgi_app,
        host="127.0.0.1",
        port=0,
        start_timeout_s=5.0,
        stop_timeout_s=5,
    )

    with pytest.raises(TimeoutError, match="simulated startup failure"):
        await server.start()

    with pytest.raises(RuntimeError, match="has not started"):
        _ = server.bound_port


def test_disables_sse_starlette_automatic_graceful_drain() -> None:
    """Regression, traced live on Windows CI: sse_starlette's
    AppStatus.should_exit is a bare process-global class attribute with no
    notion of "which server" -- ANY OTHER uvicorn.Server's signal handler
    firing handle_exit() anywhere in the process (not just ours) used to
    latch it, closing every subsequent SSE response -- including a fresh,
    healthy server's that never touched that other one -- right after its
    headers. Importing this module must disable the automatic drain so
    handle_exit() (the real 2-argument signal-handler call, not our own)
    becomes a no-op for this flag; original_handler is swapped out for the
    duration since it expects a bound Server instance, not this direct call.
    """
    assert AppStatus.enable_automatic_graceful_drain is False

    original_should_exit = AppStatus.should_exit
    original_handler = AppStatus.original_handler
    AppStatus.original_handler = None
    try:
        AppStatus.handle_exit(0, None)
        assert AppStatus.should_exit is False
    finally:
        AppStatus.should_exit = original_should_exit
        AppStatus.original_handler = original_handler
