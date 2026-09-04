"""Shared startup wait for integrations that embed their own uvicorn server.

Used by both ``band.integrations.mcp.local_server`` and
``band.integrations.a2a.gateway.server`` (and the A2A baseline test fixture)
so the one correctness-sensitive piece -- surfacing a serve task that died
before the server ever came up -- is fixed in one place.
"""

from __future__ import annotations

import asyncio

import uvicorn

POLL_INTERVAL_S = 0.05


async def wait_until_started(
    server: uvicorn.Server,
    serve_task: asyncio.Task[object],
    *,
    timeout_s: float,
) -> None:
    """Block until ``server`` reports ready.

    ``serve_task`` only returns once the server stops, so readiness is
    polled via ``server.started`` instead of awaiting the task directly.
    But a task that dies before ever setting ``started`` -- a port already
    in use, a bad TLS config -- would otherwise busy-wait the full
    ``timeout_s`` and then raise a generic timeout instead of the real
    failure; checking ``serve_task.done()`` on every pass re-raises that
    failure immediately.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not server.started:
        if serve_task.done():
            await serve_task
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(
                f"uvicorn server did not report ready within {timeout_s}s"
            )
        await asyncio.sleep(POLL_INTERVAL_S)
