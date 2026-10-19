"""REST clients the baseline harness uses to drive the platform."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
from band_rest import AsyncRestClient

# httpcore selects AF_INET for this wildcard, so the dial never tries IPv6.
_IPV4_LOCAL_ADDRESS = "0.0.0.0"


class Ipv4Backend(httpcore.AsyncNetworkBackend):
    """Opens every TCP socket on IPv4.

    httpcore's proxy connections dial with ``local_address`` left unset.
    Passing it here is what selects ``AF_INET``.
    """

    def __init__(self, backend: httpcore.AsyncNetworkBackend) -> None:
        self._backend = backend

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=_IPV4_LOCAL_ADDRESS,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


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
    only when ``transport`` is omitted. The IPv4 backend is installed on each
    pool after httpx has chosen the route, including proxy mounts.
    """
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    _bind_ipv4(client)
    return client


def _bind_ipv4(client: httpx.AsyncClient) -> None:
    for transport in (client._transport, *client._mounts.values()):
        if isinstance(transport, httpx.AsyncHTTPTransport):
            pool = transport._pool
            pool._network_backend = Ipv4Backend(pool._network_backend)
