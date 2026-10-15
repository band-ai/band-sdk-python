"""Windows harness REST client keeps environment-proxy routing."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.toolkit.restclient import baseline_rest_client

# urllib reads both cases. Clearing every name leaves only the proxies this
# test sets, including when the developer shell already has a proxy configured.
_PROXY_VARIABLES = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

# Nothing listens here. A client that ignores the proxy fails the dial
# instead of receiving the proxy's response.
_UNREACHABLE_ORIGIN = "http://127.0.0.1:1/probe"


def _only_these_proxies(monkeypatch: pytest.MonkeyPatch, **proxies: str) -> None:
    for name in _PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in proxies.items():
        monkeypatch.setenv(name, value)


def _httpx_client(client: AsyncRestClient) -> httpx.AsyncClient:
    return client._client_wrapper.httpx_client.httpx_client


def _route_bind(client: httpx.AsyncClient, url: str) -> str | None:
    """The address family the transport selected for ``url`` dials from."""
    return client._transport_for_url(httpx.URL(url))._pool._local_address


@asynccontextmanager
async def _answering_server(body: bytes) -> AsyncIterator[tuple[int, list[bytes]]]:
    seen: list[bytes] = []

    async def answer(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    sockets = server.sockets
    assert sockets is not None
    port = int(sockets[0].getsockname()[1])
    try:
        yield port, seen
    finally:
        server.close()
        await server.wait_closed()


def _request_lines(seen: list[bytes]) -> list[bytes]:
    return [request.split(b"\r\n", 1)[0] for request in seen]


class TestBaselineRestProxy:
    async def test_windows_routes_through_the_environment_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with _answering_server(b"proxied") as (port, seen):
            _only_these_proxies(monkeypatch, HTTP_PROXY=f"http://127.0.0.1:{port}")
            monkeypatch.setattr(sys, "platform", "win32")
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = _httpx_client(client)
            try:
                response = await http.get(_UNREACHABLE_ORIGIN)
                bind = _route_bind(http, _UNREACHABLE_ORIGIN)
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == b"proxied"
        assert _request_lines(seen) == [f"GET {_UNREACHABLE_ORIGIN} HTTP/1.1".encode()]
        assert bind == "0.0.0.0"

    async def test_windows_skips_the_proxy_when_no_proxy_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with (
            _answering_server(b"direct") as (origin_port, origin_seen),
            _answering_server(b"proxied") as (proxy_port, proxy_seen),
        ):
            origin = f"http://127.0.0.1:{origin_port}/direct"
            _only_these_proxies(
                monkeypatch,
                HTTP_PROXY=f"http://127.0.0.1:{proxy_port}",
                NO_PROXY="127.0.0.1",
            )
            monkeypatch.setattr(sys, "platform", "win32")
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = _httpx_client(client)
            try:
                response = await http.get(origin)
                bind = _route_bind(http, origin)
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == b"direct"
        assert proxy_seen == []
        assert _request_lines(origin_seen) == [b"GET /direct HTTP/1.1"]
        assert bind == "0.0.0.0"
