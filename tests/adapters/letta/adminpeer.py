"""Peer: an in-memory Letta admin org/user API served through ``httpx_mock``.

Mirrors the self-hosted server's behavior that matters for provisioning: no
unique names, server-assigned ids, and ``after``-cursor listing. Each request
suspends once, as real network I/O does, so concurrent callers interleave.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re

import httpx
from pytest_httpx import HTTPXMock

_ADMIN_PATH = re.compile(r"/v1/admin/(?P<kind>orgs|users)/")


class LettaAdminPeer:
    """Stores the organizations and users created through it."""

    def __init__(self, httpx_mock: HTTPXMock, *, base_url: str) -> None:
        self.rows: dict[str, list[dict[str, str]]] = {"orgs": [], "users": []}
        self._ids = itertools.count(1)
        httpx_mock.add_callback(
            self._handle,
            url=re.compile(re.escape(base_url) + _ADMIN_PATH.pattern),
            is_reusable=True,
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        match = _ADMIN_PATH.search(request.url.path)
        assert match, request.url
        rows = self.rows[match["kind"]]
        match request.method:
            case "GET":
                return httpx.Response(
                    200, json=_page_after(rows, request.url.params.get("after"))
                )
            case "POST":
                row = {
                    **json.loads(request.content),
                    "id": f"{match['kind']}-{next(self._ids)}",
                }
                rows.append(row)
                return httpx.Response(200, json=row)
        raise AssertionError(
            f"unsupported admin request: {request.method} {request.url}"
        )


def _page_after(rows: list[dict[str, str]], after: str | None) -> list[dict[str, str]]:
    if after is None:
        return list(rows)
    ids = [row["id"] for row in rows]
    return rows[ids.index(after) + 1 :]
