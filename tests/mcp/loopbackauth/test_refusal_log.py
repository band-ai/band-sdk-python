"""A refused request is logged once per route, with the room id redacted."""

from __future__ import annotations

import logging

import pytest

from band.integrations.mcp import local_server
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    REDACTED_ROOM,
)
from tests.mcp.loopbackauth.kit import (
    AUTH_TOKEN,
    ROOM,
    ROOM_HTTP,
    Credential,
    guarded_servers,
)

GUARD_LOGGER = local_server.logger.name


class TestRefusalLog:
    @pytest.mark.timeout(90)
    async def test_repeated_refusals_log_one_redacted_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=GUARD_LOGGER)
        async with guarded_servers() as guarded:
            await guarded.status(ROOM_HTTP, Credential.WRONG_TOKEN)
            await guarded.status(ROOM_HTTP, Credential.MISSING)

        [record] = [r for r in caplog.records if r.name == GUARD_LOGGER]
        message = record.getMessage()
        assert f"{LOCAL_MCP_ROOMS_PATH}/{REDACTED_ROOM}{LOCAL_MCP_HTTP_PATH}" in message
        assert ROOM not in message
        assert AUTH_TOKEN not in message
