"""Dual-stack loopback peers for harness dial tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

# urllib reads both cases. Clearing every name leaves only the proxies a test
# sets, including when the developer shell already has a proxy configured.
PROXY_VARIABLES = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

# Not a real name. Tests install a resolver that maps it to both loopbacks.
DUALSTACK_HOST = "dualstack.test"

IPV4 = "ipv4"
IPV6 = "ipv6"


def only_these_proxies(monkeypatch: pytest.MonkeyPatch, **proxies: str) -> None:
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in proxies.items():
        monkeypatch.setenv(name, value)


def install_dualstack_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve ``DUALSTACK_HOST`` to ``::1`` and ``127.0.0.1``, IPv6 first.

    Every other name keeps the system resolver. A dial bound to ``0.0.0.0``
    looks that local name up to select ``AF_INET``, so the fake has to leave
    it alone.
    """
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[Any]:
        name = host.decode("ascii") if isinstance(host, bytes) else host
        if name != DUALSTACK_HOST:
            return real_getaddrinfo(host, port, family, type, proto, flags)
        port_num = 0 if port is None else int(port)
        ipv6 = (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("::1", port_num, 0, 0),
        )
        ipv4 = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("127.0.0.1", port_num),
        )
        if family == socket.AF_INET:
            return [ipv4]
        if family == socket.AF_INET6:
            return [ipv6]
        return [ipv6, ipv4]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@dataclass
class Loopback:
    port: int
    requests: dict[str, list[bytes]] = field(default_factory=dict)


def _peer(
    body: bytes | None, seen: list[bytes]
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = b""
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as exc:
            head = exc.partial
        seen.append(head.split(b"\r\n", 1)[0])
        try:
            if body is not None:
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
                    f"Connection: close\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    return serve


@asynccontextmanager
async def dualstack_http(
    *, ipv4: bytes | None, ipv6: bytes | None
) -> AsyncIterator[Loopback]:
    """Serve HTTP on ``127.0.0.1`` and ``::1`` at one port.

    A body is returned as HTTP 200. ``None`` accepts the request and closes,
    which is a proxy whose IPv6 listener dies after TCP connects.
    """
    requests: dict[str, list[bytes]] = {IPV4: [], IPV6: []}
    ipv6_server = await asyncio.start_server(_peer(ipv6, requests[IPV6]), "::1", 0)
    try:
        sockets = ipv6_server.sockets
        assert sockets is not None
        port = int(sockets[0].getsockname()[1])
        ipv4_server = await asyncio.start_server(
            _peer(ipv4, requests[IPV4]), "127.0.0.1", port
        )
        try:
            yield Loopback(port=port, requests=requests)
        finally:
            ipv4_server.close()
            await ipv4_server.wait_closed()
    finally:
        ipv6_server.close()
        await ipv6_server.wait_closed()
