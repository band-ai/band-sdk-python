"""The SDK emits the proxy-managed sentinel where the trusted proxy substitutes it.

This is the REST half of header-based custody: given the sentinel as its
``api_key``, ``BandLink`` must send it in the ``X-API-Key`` request header — the
slot the Docker Sandboxes proxy replaces with the real credential. The WS half
(the sentinel riding the ``?api_key=`` query on the upgrade) is proven by the
in-process peer in ``tests/websocket/test_client.py``.

Interception is the maintained ``pytest-httpx`` ``httpx_mock`` fixture, which
patches httpx globally — so it observes the Fern client's own internally-built
client on the real ``BandLink`` construction path, with no injected seam.
"""

from __future__ import annotations

from pytest_httpx import HTTPXMock

from band.credentials import PROXY_MANAGED_API_KEY
from band.platform.link import BandLink

# A minimal but schema-valid agent-me body so the Fern method parses and the
# call completes; the assertion is about the outbound header, not this payload.
_AGENT_ME = {
    "id": "agent-1",
    "handle": "agent",
    "name": "Agent",
    "owner_uuid": "owner-1",
    "inserted_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


async def test_rest_emits_sentinel_in_api_key_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"data": _AGENT_ME})

    link = BandLink(agent_id="agent-1", api_key=PROXY_MANAGED_API_KEY)
    await link.rest.agent_api_identity.get_agent_me()

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["X-API-Key"] == PROXY_MANAGED_API_KEY
