"""Refused requests are logged on the 1st and 10th per redacted route, with
room ids and unserved paths masked."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import pytest

from band.integrations.mcp import local_server
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    LOCAL_MCP_SSE_PATH,
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


def first_and_tenth(logged_route: str) -> list[tuple[str, int]]:
    """The refusals of one route that are logged."""
    return [(logged_route, 1), (logged_route, REFUSALS)]


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
