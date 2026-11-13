"""Concurrent starts of one Letta agent in a process create its org/user scope once."""

from __future__ import annotations

import asyncio

from pytest_httpx import HTTPXMock

from band.integrations.letta.orgscope import resolve_org_scoped_headers
from tests.adapters.letta.adminpeer import LettaAdminPeer

_BASE_URL = "http://localhost:8283"


async def _start_twice_at_once() -> list[dict[str, str]]:
    return await asyncio.gather(
        *(
            resolve_org_scoped_headers(
                base_url=_BASE_URL, agent_name="agent", bearer_token=None
            )
            for _ in range(2)
        )
    )


_ONE_SCOPE = {
    "orgs": [{"id": "orgs-1", "name": "band-agent"}],
    "users": [{"id": "users-2", "name": "band-agent", "organization_id": "orgs-1"}],
}


class TestOrgScopeConcurrency:
    async def test_concurrent_starts_create_one_org_and_user(
        self, httpx_mock: HTTPXMock
    ) -> None:
        admin = LettaAdminPeer(httpx_mock, base_url=_BASE_URL)

        first, second = await _start_twice_at_once()

        assert admin.rows == _ONE_SCOPE
        assert first == second == {"user_id": "users-2"}

    def test_concurrent_starts_on_a_later_event_loop_reuse_the_scope(
        self, httpx_mock: HTTPXMock
    ) -> None:
        admin = LettaAdminPeer(httpx_mock, base_url=_BASE_URL)

        earlier = asyncio.run(_start_twice_at_once())
        later = asyncio.run(_start_twice_at_once())

        assert admin.rows == _ONE_SCOPE
        assert earlier == later == [{"user_id": "users-2"}] * 2
