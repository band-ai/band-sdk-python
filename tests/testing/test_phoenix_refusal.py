"""Scoped local-peer refusals restore the original upgrade behavior."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from band.testing import fake_phoenix_server
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestScopedPhoenixRefusal:
    @pytest.mark.parametrize(
        "refuse_upgrades",
        [False, True],
        ids=["accept-by-default", "conflict-by-default"],
    )
    async def test_scoped_forbidden_restores_the_original_upgrade_behavior(
        self, refuse_upgrades: bool
    ) -> None:
        async with (
            controlled_clock(),
            fake_phoenix_server(refuse_upgrades=refuse_upgrades) as server,
        ):
            with server.refusing_upgrades(HTTPStatus.FORBIDDEN):
                with pytest.raises(InvalidStatus) as forbidden:
                    async with connect(server.url):
                        pytest.fail("Forbidden upgrade was accepted")
                assert forbidden.value.response.status_code == HTTPStatus.FORBIDDEN
            if refuse_upgrades:
                with pytest.raises(InvalidStatus) as conflict:
                    async with connect(server.url):
                        pytest.fail("Conflict upgrade was accepted")
                assert conflict.value.response.status_code == HTTPStatus.CONFLICT
                assert "connection_conflict" in conflict.value.response.body.decode()
            else:
                async with connect(server.url):
                    await server.wait_for_connection(1)
