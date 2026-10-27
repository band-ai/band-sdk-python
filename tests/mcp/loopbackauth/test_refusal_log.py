"""Refused requests are logged on the 1st, 10th, 100th, ... per redacted
route, with room ids and unserved paths masked."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import pytest
from starlette.types import Receive, Scope, Send

from band.integrations.mcp import BandMCPTransport, local_server
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    REDACTED_ROOM,
    REFUSAL_LOG,
    UNKNOWN_ROUTE,
)
from tests.mcp.loopbackauth.kit import (
    CRAFTED_PATH,
    CRAFTED_ROOM_PATH,
    MULTI_ROOM_HTTP,
    ROOM_HTTP,
    ROOM_SSE,
    SERVER_NAME,
    Credential,
    Route,
    bare_guard,
    drive,
    guarded_servers,
)
from tests.mcpclient import endpoint_path

GUARD_LOGGER = local_server.logger.name
REFUSALS = 10
ROOM_HTTP_LOGGED = endpoint_path(BandMCPTransport.HTTP, REDACTED_ROOM)
ROOM_SSE_LOGGED = endpoint_path(BandMCPTransport.SSE, REDACTED_ROOM)
# One room route per refusal, each in its own room.
ROOM_HTTP_PER_ROOM = [
    replace(ROOM_HTTP, path=endpoint_path(BandMCPTransport.HTTP, f"room-{n}"))
    for n in range(REFUSALS)
]
# Enough refusals to pass the 100th without reaching the 1000th.
MANY_REFUSALS = 120


def first_and_tenth(logged_route: str) -> list[tuple[str, int]]:
    """The refusals of one route that are logged."""
    return [(logged_route, 1), (logged_route, REFUSALS)]


# A tokenless request to the bare guard's route.
UNAUTHENTICATED = {
    "type": "http",
    "method": "POST",
    "path": LOCAL_MCP_HTTP_PATH,
    "headers": [],
}


async def unreachable_app(scope: Scope, receive: Receive, send: Send) -> None:
    """The guarded app, which no refused request may reach."""
    raise AssertionError("a refused request reached the guarded app")


def guard_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The messages the guard logged."""
    return [r.getMessage() for r in caplog.records if r.name == GUARD_LOGGER]


class TestRefusalLog:
    @pytest.mark.timeout(90)
    @pytest.mark.parametrize(
        ("routes", "logged"),
        [
            pytest.param(
                [ROOM_HTTP] * REFUSALS,
                first_and_tenth(ROOM_HTTP_LOGGED),
                id="served-room-route",
            ),
            pytest.param(
                ROOM_HTTP_PER_ROOM, first_and_tenth(ROOM_HTTP_LOGGED), id="one-per-room"
            ),
            pytest.param(
                [ROOM_HTTP, ROOM_SSE] * REFUSALS,
                [
                    (ROOM_HTTP_LOGGED, 1),
                    (ROOM_SSE_LOGGED, 1),
                    (ROOM_HTTP_LOGGED, REFUSALS),
                    (ROOM_SSE_LOGGED, REFUSALS),
                ],
                id="two-routes-interleaved",
            ),
            pytest.param(
                [MULTI_ROOM_HTTP] * REFUSALS,
                first_and_tenth(LOCAL_MCP_HTTP_PATH),
                id="served-multi-room-route",
            ),
            pytest.param(
                [CRAFTED_ROOM_PATH] * REFUSALS,
                first_and_tenth(UNKNOWN_ROUTE),
                id="crafted-room-path",
            ),
            pytest.param(
                [CRAFTED_PATH] * REFUSALS,
                first_and_tenth(UNKNOWN_ROUTE),
                id="crafted-path",
            ),
        ],
    )
    async def test_the_first_and_tenth_refusal_are_logged_redacted(
        self,
        routes: Sequence[Route],
        logged: list[tuple[str, int]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger=GUARD_LOGGER)
        async with guarded_servers() as guarded:
            for route in routes:
                await guarded.status(route, Credential.WRONG_TOKEN)

        assert guard_warnings(caplog) == [
            REFUSAL_LOG % (SERVER_NAME, route, count) for route, count in logged
        ]

    async def test_refusals_past_the_tenth_are_logged_at_each_power_of_ten(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=GUARD_LOGGER)
        guard = bare_guard(unreachable_app, LOCAL_MCP_HTTP_PATH)

        for _ in range(MANY_REFUSALS):
            await drive(guard, UNAUTHENTICATED)

        assert guard_warnings(caplog) == [
            REFUSAL_LOG % (SERVER_NAME, LOCAL_MCP_HTTP_PATH, count)
            for count in (1, 10, 100)
        ]
