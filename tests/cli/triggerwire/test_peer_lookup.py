"""band-trigger matches the target handle however either side spells its ``@`` or case."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from band.cli.trigger import AuthMode, find_peer_by_handle
from band.client.rest import AsyncRestClient
from tests.cli.triggerwire.kit import TARGET, answer_peer_pages

LISTED = TARGET["handle"]


class TestPeerLookup:
    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize(
        ("requested", "listed"),
        [
            pytest.param(LISTED, LISTED, id="bare"),
            pytest.param(f"@{LISTED}", LISTED, id="requested-with-at"),
            pytest.param(LISTED, f"@{LISTED}", id="listed-with-at"),
            pytest.param(f"@{LISTED}", f"@{LISTED}", id="both-with-at"),
            pytest.param(f"@{LISTED.upper()}", LISTED, id="requested-case"),
            pytest.param(LISTED, LISTED.upper(), id="listed-case"),
        ],
    )
    async def test_finds_the_peer_and_keeps_its_listed_handle(
        self,
        httpx_mock: HTTPXMock,
        rest_client: AsyncRestClient,
        mode: AuthMode,
        requested: str,
        listed: str,
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[{**TARGET, "handle": listed}]])

        peer = await find_peer_by_handle(rest_client, requested, mode)

        assert peer == {"id": TARGET["id"], "name": TARGET["name"], "handle": listed}

    @pytest.mark.parametrize("mode", list(AuthMode))
    async def test_an_empty_directory_finds_nobody(
        self, httpx_mock: HTTPXMock, rest_client: AsyncRestClient, mode: AuthMode
    ) -> None:
        answer_peer_pages(httpx_mock, mode, [[]])

        assert await find_peer_by_handle(rest_client, LISTED, mode) is None
