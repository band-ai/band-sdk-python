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
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

_ADMIN_PREFIX = "/v1/admin"
_DEFAULT_TIMEOUT_S = 30.0
# Bounds _paginated_find against a pagination cursor that keeps advancing
# without ever emptying or stalling -- a second, undiscovered quirk in
# Letta's admin API pagination (the same surface that already produced the
# null-created_at stall this client works around) would otherwise hang here
# forever instead of failing loud.
_MAX_PAGINATION_PAGES = 10_000
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
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout_s
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def find_or_create_organization(self, name: str) -> str:
        """The id of the organization named ``name``, creating it if absent."""
        org = await self._find_or_create(
            f"{_ADMIN_PREFIX}/orgs/",
            match=lambda org: org["name"] == name,
            payload={"name": name},
            log_label=f"organization {name!r}",
        )
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
        user = await self._find_or_create(
            f"{_ADMIN_PREFIX}/users/",
            match=lambda user: (
                user["name"] == name and user["organization_id"] == organization_id
            ),
            payload={"name": name, "organization_id": organization_id},
            log_label=f"user {name!r} in organization {organization_id}",
        )
        return user["id"]

    async def _find_or_create(
        self,
        path: str,
        *,
        match: _Match,
        payload: dict[str, Any],
        log_label: str,
    ) -> dict:
        """The first item on ``path`` matching ``match``, creating one from
        ``payload`` if none does."""
        existing = await self._paginated_find(path, match=match)
        if existing is not None:
            return existing
        response = await self._http.post(path, json=payload)
        response.raise_for_status()
        created = response.json()
        logger.info("Created Letta %s (id=%s)", log_label, created["id"])
        return created

    async def _paginated_find(self, path: str, *, match: _Match) -> dict | None:
        """The first item on ``path`` satisfying ``match``, paging via ``after``.

        Letta's own seeded default user has a null ``created_at``, and its
        ``after``-cursor pagination silently drops the boundary filter
        whenever ``after`` resolves to a null-``created_at`` row (confirmed
        against ``letta/orm/sqlalchemy_base.py``'s ``_list_preprocess``) --
        the next "page" comes back as the same unfiltered list again, always
        ending on that same row once more. Advancing ``after`` to an
        unchanged value is the signal that pagination has stalled; treating
        it as exhaustion avoids looping forever instead of trying to detect
        the null-created_at row directly (an implementation detail of a
        response shape this client does not otherwise need to know).
        """
        after: str | None = None
        for _ in range(_MAX_PAGINATION_PAGES):
            response = await self._http.get(
                path, params={"after": after} if after else None
            )
            response.raise_for_status()
            page: list[dict] = response.json()
            if not page:
                return None
            found = next((item for item in page if match(item)), None)
            if found is not None:
                return found
            next_after = page[-1]["id"]
            if next_after == after:
                return None
            after = next_after
        raise RuntimeError(
            f"Letta admin API pagination for {path!r} did not terminate after "
            f"{_MAX_PAGINATION_PAGES} pages"
        )


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
    try:
        organization_id = await scope_client.find_or_create_organization(scope_name)
        user_id = await scope_client.find_or_create_user(
            scope_name, organization_id=organization_id
        )
    finally:
        await scope_client.aclose()
    return {"user_id": user_id}
