"""What ``EmbeddedServer`` guarantees about the port it holds.

Every in-process transport (A2A gateway, Slack HTTP ingress, the local MCP
server) restarts on a fixed port, so "stopped" has to mean the socket is
free — not merely that uvicorn was asked to exit. These run real servers on
real ports because that is the only way to observe the socket.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from band.core.exceptions import LifecycleError
from band.core.serving import EmbeddedServer

HOST = "127.0.0.1"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def app() -> Starlette:
    return Starlette(
        routes=[Route("/", lambda _request: PlainTextResponse("ok"))],
    )


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


@pytest.fixture
def server() -> EmbeddedServer:
    return EmbeddedServer(app(), host=HOST, port=free_port(), log_level="warning")


async def serving(server: EmbeddedServer) -> asyncio.Task[None]:
    """Run ``server`` in the background and wait until it accepts requests."""
    task = asyncio.create_task(server.serve())
    await server.wait_until_serving(5.0)
    return task


@pytest.mark.asyncio
async def test_stop_frees_the_port(server: EmbeddedServer) -> None:
    """Signalling uvicorn is not the same as it having let go of the socket."""
    task = await serving(server)

    await server.stop()
    await task

    assert port_is_free(server._port)


@pytest.mark.asyncio
async def test_a_cancelled_run_frees_the_port(server: EmbeddedServer) -> None:
    """uvicorn's serve() skips its own shutdown when cancelled.

    A host cancelled inside a TaskGroup or a wait_for would otherwise leave
    the listening socket bound with no handle left to close it — and the
    restart on that port dies with EADDRINUSE.
    """
    task = await serving(server)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert port_is_free(server._port)


@pytest.mark.asyncio
async def test_the_startup_wait_does_not_outlive_the_run(
    server: EmbeddedServer,
) -> None:
    """A run that has already ended can never go on to serve.

    The wait has to notice, or a caller whose run died on the way up sits
    out the whole timeout and is told the clock ran out — while the reason
    it died stays in an unretrieved task exception.
    """
    await server.start()
    await server.stop()

    with pytest.raises(LifecycleError, match="exited before"):
        await server.wait_until_serving(5.0)


@pytest.mark.asyncio
async def test_a_stop_during_startup_is_not_missed(server: EmbeddedServer) -> None:
    """A stop that arrives mid-startup must not slip past the run.

    Arming a run in two steps — mark it in flight, then build the server —
    leaves a window where a stop finds nothing to signal and returns while
    the server goes on to bind the port and stay up.
    """
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0)  # the run is armed, not yet listening

    await server.stop()
    await task

    assert not server.running
    assert port_is_free(server._port)


@pytest.mark.asyncio
async def test_foreground_startup_failure_reaches_waiter(
    server: EmbeddedServer,
) -> None:
    async def fail_startup(*_args, **_kwargs):
        raise RuntimeError("startup failed")

    with patch("uvicorn.Server.serve", new=fail_startup):
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="startup failed"):
            await server.wait_until_serving(1.0)
        with pytest.raises(RuntimeError, match="startup failed"):
            await task
