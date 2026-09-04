"""Unit tests for Letta self-hosted org/user provisioning (INT-985).

Interception is the maintained ``pytest-httpx`` ``httpx_mock`` fixture (the
pattern in ``tests/platform/test_link_credentials.py``) — it patches httpx
globally, so it observes ``LettaOrgScopeClient``'s real per-call
``httpx.AsyncClient`` with no injected seam.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from band.integrations.letta.orgscope import (
    LettaOrgScopeClient,
    resolve_org_scoped_headers,
)

_BASE_URL = "http://localhost:8283"


def _client(*, bearer_token: str | None = None) -> LettaOrgScopeClient:
    return LettaOrgScopeClient(base_url=_BASE_URL, bearer_token=bearer_token)


class TestFindOrCreateOrganization:
    async def test_no_match_creates(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/orgs/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json={"id": "org-new", "name": "band-x"},
        )

        org_id = await _client().find_or_create_organization("band-x")

        assert org_id == "org-new"

    async def test_match_on_first_page_skips_create(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json=[{"id": "org-1", "name": "band-x"}],
        )

        org_id = await _client().find_or_create_organization("band-x")

        assert org_id == "org-1"
        assert len(httpx_mock.get_requests(method="POST")) == 0

    async def test_match_on_second_page_paginates(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json=[{"id": "org-1", "name": "other"}],
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/orgs/?after=org-1",
            json=[{"id": "org-2", "name": "band-x"}],
        )

        org_id = await _client().find_or_create_organization("band-x")

        assert org_id == "org-2"
        assert len(httpx_mock.get_requests(method="GET")) == 2
        assert len(httpx_mock.get_requests(method="POST")) == 0


class TestFindOrCreateUser:
    async def test_no_match_creates(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/users/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/users/",
            json={"id": "user-new", "name": "band-x", "organization_id": "org-1"},
        )

        user_id = await _client().find_or_create_user("band-x", organization_id="org-1")

        assert user_id == "user-new"
        create_request = httpx_mock.get_requests(method="POST")[0]
        assert json.loads(create_request.content) == {
            "name": "band-x",
            "organization_id": "org-1",
        }

    async def test_match_on_first_page_skips_create(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/",
            json=[{"id": "user-1", "name": "band-x", "organization_id": "org-1"}],
        )

        user_id = await _client().find_or_create_user("band-x", organization_id="org-1")

        assert user_id == "user-1"
        assert len(httpx_mock.get_requests(method="POST")) == 0

    async def test_match_on_second_page_paginates(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/",
            json=[{"id": "user-1", "name": "other", "organization_id": "org-1"}],
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/?after=user-1",
            json=[{"id": "user-2", "name": "band-x", "organization_id": "org-1"}],
        )

        user_id = await _client().find_or_create_user("band-x", organization_id="org-1")

        assert user_id == "user-2"
        assert len(httpx_mock.get_requests(method="POST")) == 0

    async def test_same_name_different_org_is_not_a_match(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Regression guard: a same-named user under a different org must not
        be adopted — that would land this instance in the wrong org, exactly
        the collision this fix exists to prevent (GET /v1/admin/users/ lists
        across the whole instance, not one organization).
        """
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/",
            json=[{"id": "user-X", "name": "band-Bob", "organization_id": "org-X"}],
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/?after=user-X",
            json=[],
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/users/",
            json={"id": "user-Y", "name": "band-Bob", "organization_id": "org-Y"},
        )

        user_id = await _client().find_or_create_user(
            "band-Bob", organization_id="org-Y"
        )

        assert user_id == "user-Y"
        assert user_id != "user-X"

    async def test_stalled_after_cursor_terminates_instead_of_looping_forever(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Regression guard: Letta's own seeded default user has a null
        created_at, and GET /v1/admin/users/ silently drops the after
        boundary filter whenever after resolves to that row (confirmed live
        against letta/letta:0.16.8) -- the "next page" comes back as the
        exact same unfiltered list, forever. Without stall detection this
        hangs instead of concluding "not found".
        """
        stalled_page = [
            {
                "id": "user-default",
                "name": "default_user",
                "organization_id": "org-default",
            }
        ]
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/users/", json=stalled_page
        )
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/v1/admin/users/?after=user-default",
            json=stalled_page,
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/users/",
            json={"id": "user-new", "name": "band-x", "organization_id": "org-1"},
        )

        user_id = await _client().find_or_create_user("band-x", organization_id="org-1")

        assert user_id == "user-new"
        assert len(httpx_mock.get_requests(method="GET")) == 2


class TestAuthPassthrough:
    async def test_bearer_token_sent_when_set(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/orgs/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json={"id": "org-1", "name": "band-x"},
        )

        await _client(bearer_token="secret").find_or_create_organization("band-x")

        for request in httpx_mock.get_requests():
            assert request.headers["Authorization"] == "Bearer secret"

    async def test_no_authorization_header_when_unset(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/orgs/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json={"id": "org-1", "name": "band-x"},
        )

        await _client(bearer_token=None).find_or_create_organization("band-x")

        for request in httpx_mock.get_requests():
            assert "Authorization" not in request.headers


class TestResolveOrgScopedHeaders:
    async def test_end_to_end_provisions_org_then_user(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/orgs/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/orgs/",
            json={"id": "org-1", "name": "band-my-agent"},
        )
        httpx_mock.add_response(
            method="GET", url=f"{_BASE_URL}/v1/admin/users/", json=[]
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/v1/admin/users/",
            json={"id": "user-1", "name": "band-my-agent", "organization_id": "org-1"},
        )

        headers = await resolve_org_scoped_headers(
            base_url=_BASE_URL, agent_name="my-agent", bearer_token=None
        )

        assert headers == {"user_id": "user-1"}
        assert len(httpx_mock.get_requests(url=f"{_BASE_URL}/v1/admin/orgs/")) == 2
        assert len(httpx_mock.get_requests(url=f"{_BASE_URL}/v1/admin/users/")) == 2

    @pytest.mark.parametrize("agent_name", ["", "   ", "\t\n"])
    async def test_blank_agent_name_raises_before_any_http_call(
        self, httpx_mock: HTTPXMock, agent_name: str
    ) -> None:
        with pytest.raises(ValueError, match="agent_name"):
            await resolve_org_scoped_headers(
                base_url=_BASE_URL, agent_name=agent_name, bearer_token=None
            )

        assert httpx_mock.get_requests() == []
