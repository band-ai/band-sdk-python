"""REST clients the baseline harness uses to drive the platform."""

from __future__ import annotations

import sys
from typing import Any

import httpx
from band_rest import AsyncRestClient

# httpcore selects AF_INET for this wildcard, so the dial never tries IPv6.
_IPV4_LOCAL_ADDRESS = "0.0.0.0"


def baseline_rest_client(*, api_key: str, base_url: str) -> AsyncRestClient:
    """The harness REST client, shared by the user and agent constructors.

    GitHub-hosted Windows runners have no working IPv6, and the platform
    publishes AAAA records. httpx does not fall back to IPv4 once TCP to an
    IPv6 address has succeeded and the TLS handshake then closes. On Windows
    every dial is bound to IPv4 after httpx has chosen the route, so
    ``HTTP_PROXY``, ``HTTPS_PROXY``, ``ALL_PROXY``, and ``NO_PROXY`` still
    apply. Every other platform keeps the generated client's own transport.
    """
    kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if sys.platform == "win32":
        kwargs["httpx_client"] = _windows_httpx_client()
    return AsyncRestClient(**kwargs)


def _windows_httpx_client() -> httpx.AsyncClient:
    """An httpx client with the generated client's timeout and redirects.

    Built without an explicit transport: httpx enables environment proxies
    only when ``transport`` is omitted. ``local_address`` passed to
    ``AsyncHTTPTransport`` does not reach an HTTP proxy pool, so the IPv4
    bind is applied to each pool after httpx builds it.
    """
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    _bind_ipv4(client)
    return client


def _bind_ipv4(client: httpx.AsyncClient) -> None:
    for transport in (client._transport, *client._mounts.values()):
        if isinstance(transport, httpx.AsyncHTTPTransport):
            transport._pool._local_address = _IPV4_LOCAL_ADDRESS
