"""Refused requests are logged on the 1st, 10th, 100th, ... per redacted
route, with room ids and unserved paths masked."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import pytest
from starlette.types import Message, Receive, Scope, Send

from band.integrations.mcp import local_server
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    LOCAL_MCP_SSE_PATH,
    REDACTED_ROOM,
    REFUSAL_LOG,
    UNKNOWN_ROUTE,
    BearerTokenGuard,
)
from tests.mcp.loopbackauth.kit import (
    AUTH_TOKEN,
    CRAFTED_PATH,
    CRAFTED_ROOM_PATH,
    MULTI_ROOM_HTTP,
    ROOM_HTTP,
    ROOM_SSE,
    SERVER_NAME,
    Credential,
    Route,
    guarded_servers,
)

GUARD_LOGGER = local_server.logger.name
REFUSALS = 10
ROOM_HTTP_LOGGED = f"{LOCAL_MCP_ROOMS_PATH}/{REDACTED_ROOM}{LOCAL_MCP_HTTP_PATH}"
ROOM_SSE_LOGGED = f"{LOCAL_MCP_ROOMS_PATH}/{REDACTED_ROOM}{LOCAL_MCP_SSE_PATH}"
# One room route per refusal, each in its own room.
ROOM_HTTP_PER_ROOM = [
    replace(ROOM_HTTP, path=f"{LOCAL_MCP_ROOMS_PATH}/room-{n}{LOCAL_MCP_HTTP_PATH}")
    for n in range(REFUSALS)
]
# Enough refusals to pass the 100th without reaching the 1000th.
MANY_REFUSALS = 120


def first_and_tenth(logged_route: str) -> list[tuple[str, int]]:
    """The refusals of one route that are logged."""
    return [(logged_route, 1), (logged_route, REFUSALS)]


async def unreachable_app(scope: Scope, receive: Receive, send: Send) -> None:
    """The guarded app, which no refused request may reach."""
    raise AssertionError("a refused request reached the guarded app")


async def refuse_unauthenticated(guard: BearerTokenGuard) -> None:
    """Send ``guard`` one tokenless HTTP request to its served route."""

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        pass

    scope = {
        "type": "http",
        "method": "POST",
        "path": LOCAL_MCP_HTTP_PATH,
        "headers": [],
    }
    await guard(scope, receive, send)


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

        assert [r.getMessage() for r in caplog.records if r.name == GUARD_LOGGER] == [
            REFUSAL_LOG % (SERVER_NAME, route, count) for route, count in logged
        ]

    async def test_refusals_past_the_tenth_are_logged_at_each_power_of_ten(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=GUARD_LOGGER)
        guard = BearerTokenGuard(
            unreachable_app,
            token=AUTH_TOKEN,
            open_paths=frozenset(),
            routes=frozenset({LOCAL_MCP_HTTP_PATH}),
            server_name=SERVER_NAME,
        )

        for _ in range(MANY_REFUSALS):
            await refuse_unauthenticated(guard)

        assert [r.getMessage() for r in caplog.records if r.name == GUARD_LOGGER] == [
            REFUSAL_LOG % (SERVER_NAME, LOCAL_MCP_HTTP_PATH, count)
            for count in (1, 10, 100)
        ]
