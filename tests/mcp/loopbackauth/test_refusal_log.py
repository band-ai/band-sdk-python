"""Refused requests are logged on the 1st and 10th per route, with room ids
and unserved paths redacted."""

from __future__ import annotations

import logging

import pytest

from band.integrations.mcp import local_server
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    REDACTED_ROOM,
    REFUSAL_LOG,
    UNKNOWN_ROUTE,
)
from tests.mcp.loopbackauth.kit import (
    CRAFTED_PATH,
    CRAFTED_ROOM_PATH,
    ROOM_HTTP,
    SERVER_NAME,
    Credential,
    Route,
    guarded_servers,
)

GUARD_LOGGER = local_server.logger.name
REFUSALS = 10


class TestRefusalLog:
    @pytest.mark.timeout(90)
    @pytest.mark.parametrize(
        ("route", "logged_route"),
        [
            pytest.param(
                ROOM_HTTP,
                f"{LOCAL_MCP_ROOMS_PATH}/{REDACTED_ROOM}{LOCAL_MCP_HTTP_PATH}",
                id="served-room-route",
            ),
            pytest.param(CRAFTED_ROOM_PATH, UNKNOWN_ROUTE, id="crafted-room-path"),
            pytest.param(CRAFTED_PATH, UNKNOWN_ROUTE, id="crafted-path"),
        ],
    )
    async def test_the_first_and_tenth_refusal_are_logged_redacted(
        self, route: Route, logged_route: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=GUARD_LOGGER)
        async with guarded_servers() as guarded:
            for _ in range(REFUSALS):
                await guarded.status(route, Credential.WRONG_TOKEN)

        assert [r.getMessage() for r in caplog.records if r.name == GUARD_LOGGER] == [
            REFUSAL_LOG % (SERVER_NAME, logged_route, 1),
            REFUSAL_LOG % (SERVER_NAME, logged_route, REFUSALS),
        ]
