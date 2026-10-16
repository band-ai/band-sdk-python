"""Windows harness REST client keeps environment-proxy routing on IPv4."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.toolkit.restclient import baseline_rest_client
from tests.toolkit.dualstack import (
    DUALSTACK_HOST,
    IPV4,
    IPV6,
    dualstack_http,
    install_dualstack_dns,
    only_these_proxies,
)

# Nothing listens here. A client that ignores the proxy fails the dial
# instead of receiving the proxy's response.
_UNREACHABLE_ORIGIN = "http://127.0.0.1:1/probe"


def _httpx_client(client: AsyncRestClient) -> httpx.AsyncClient:
    return client._client_wrapper.httpx_client.httpx_client


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
            only_these_proxies(monkeypatch, HTTP_PROXY=f"http://127.0.0.1:{port}")
            monkeypatch.setattr(sys, "platform", "win32")
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = _httpx_client(client)
            try:
                response = await http.get(_UNREACHABLE_ORIGIN)
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == b"proxied"
        assert _request_lines(seen) == [f"GET {_UNREACHABLE_ORIGIN} HTTP/1.1".encode()]

    async def test_windows_skips_the_proxy_when_no_proxy_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with (
            _answering_server(b"direct") as (origin_port, origin_seen),
            _answering_server(b"proxied") as (proxy_port, proxy_seen),
        ):
            origin = f"http://127.0.0.1:{origin_port}/direct"
            only_these_proxies(
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
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == b"direct"
        assert proxy_seen == []
        assert _request_lines(origin_seen) == [b"GET /direct HTTP/1.1"]

    async def test_windows_dials_a_dualstack_proxy_over_ipv4(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dualstack_dns(monkeypatch)
        async with dualstack_http(ipv4=b"proxied", ipv6=None) as loopback:
            only_these_proxies(
                monkeypatch, HTTP_PROXY=f"http://{DUALSTACK_HOST}:{loopback.port}"
            )
            monkeypatch.setattr(sys, "platform", "win32")
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = _httpx_client(client)
            try:
                response = await http.get(_UNREACHABLE_ORIGIN)
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == b"proxied"
        assert loopback.requests == {
            IPV4: [f"GET {_UNREACHABLE_ORIGIN} HTTP/1.1".encode()],
            IPV6: [],
        }

    async def test_windows_sends_connect_to_a_dualstack_proxy_over_ipv4(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_dualstack_dns(monkeypatch)
        async with dualstack_http(ipv4=b"", ipv6=None) as loopback:
            only_these_proxies(
                monkeypatch, HTTPS_PROXY=f"http://{DUALSTACK_HOST}:{loopback.port}"
            )
            monkeypatch.setattr(sys, "platform", "win32")
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = _httpx_client(client)
            try:
                with pytest.raises(httpx.HTTPError):
                    await http.get("https://example.invalid/probe")
            finally:
                await http.aclose()

        assert loopback.requests == {
            IPV4: [b"CONNECT example.invalid:443 HTTP/1.1"],
            IPV6: [],
        }
