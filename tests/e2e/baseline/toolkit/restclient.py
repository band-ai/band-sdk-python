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
    IPv6 address has succeeded and the TLS handshake then closes. Binding the
    dial to IPv4 avoids that. Every other platform keeps the generated
    client's own transport.
    """
    kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if sys.platform == "win32":
        kwargs["httpx_client"] = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(local_address=_IPV4_LOCAL_ADDRESS),
            timeout=60,
            follow_redirects=True,
        )
    return AsyncRestClient(**kwargs)
