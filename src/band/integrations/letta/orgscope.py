"""Self-hosted-only Letta organization/user provisioning for MCP isolation.

Letta dedupes MCP-discovered ``Tool`` rows by ``(name, organization_id)``. On
a shared self-hosted server, every ``LettaAdapter`` instance that never sets
a ``user_id`` header resolves to the same default org, so a second
instance's MCP registration silently re-points the first instance's tool row
to its own server. Provisioning a dedicated organization + user per instance
and sending its ``user_id`` in every request (``AsyncLetta(default_headers=
{"user_id": ...})``) isolates MCP server + tool storage between instances.

The admin API this needs (``/v1/admin/orgs/``, ``/v1/admin/users/``) is not
exposed by the ``letta_client`` SDK, hence the raw ``httpx`` calls here.
"""

from __future__ import annotations

import logging
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

_ADMIN_PREFIX = "/v1/admin"
_DEFAULT_TIMEOUT_S = 30.0
_Match = Callable[[dict], bool]


class LettaOrgScopeClient:
    """Minimal async client for Letta's self-hosted admin org/user API."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = (
            {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        )
        self._timeout_s = timeout_s

    async def find_or_create_organization(self, name: str) -> str:
        """The id of the organization named ``name``, creating it if absent."""
        async with self._client() as client:
            existing = await self._paginated_find(
                client, f"{_ADMIN_PREFIX}/orgs/", match=lambda org: org["name"] == name
            )
            if existing is not None:
                return existing["id"]
            response = await client.post(f"{_ADMIN_PREFIX}/orgs/", json={"name": name})
            response.raise_for_status()
            org = response.json()
            logger.info("Created Letta organization %r (id=%s)", name, org["id"])
            return org["id"]

    async def find_or_create_user(self, name: str, *, organization_id: str) -> str:
        """The id of the user named ``name`` under ``organization_id``.

        Matches on both name and organization_id: ``GET /v1/admin/users/``
        lists across the entire instance, not just one organization, and
        neither Organization nor User has a database-level unique
        constraint on name — a same-named user under a different org is a
        real possibility, not a hypothetical one, and matching by name
        alone would adopt it (landing in the wrong org).
        """
        async with self._client() as client:
            existing = await self._paginated_find(
                client,
                f"{_ADMIN_PREFIX}/users/",
                match=lambda user: (
                    user["name"] == name and user["organization_id"] == organization_id
                ),
            )
            if existing is not None:
                return existing["id"]
            response = await client.post(
                f"{_ADMIN_PREFIX}/users/",
                json={"name": name, "organization_id": organization_id},
            )
            response.raise_for_status()
            user = response.json()
            logger.info(
                "Created Letta user %r (id=%s) in organization %s",
                name,
                user["id"],
                organization_id,
            )
            return user["id"]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout_s,
        )

    @staticmethod
    async def _paginated_find(
        client: httpx.AsyncClient,
        path: str,
        *,
        match: _Match,
    ) -> dict | None:
        """The first item on ``path`` satisfying ``match``, paging via ``after``."""
        after: str | None = None
        while True:
            response = await client.get(
                path, params={"after": after} if after else None
            )
            response.raise_for_status()
            page: list[dict] = response.json()
            if not page:
                return None
            found = next((item for item in page if match(item)), None)
            if found is not None:
                return found
            after = page[-1]["id"]


async def resolve_org_scoped_headers(
    *, base_url: str, agent_name: str, bearer_token: str | None
) -> dict[str, str]:
    """Provision/reuse this instance's dedicated org+user; return {"user_id": ...}."""
    if not agent_name.strip():
        raise ValueError(
            "agent_name must be non-blank to derive a Letta org-scoped identity"
        )
    scope_name = f"band-{agent_name}"
    scope_client = LettaOrgScopeClient(base_url=base_url, bearer_token=bearer_token)
    organization_id = await scope_client.find_or_create_organization(scope_name)
    user_id = await scope_client.find_or_create_user(
        scope_name, organization_id=organization_id
    )
    return {"user_id": user_id}
