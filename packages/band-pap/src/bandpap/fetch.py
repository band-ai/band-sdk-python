"""Bounded, credential-free discovery over public HTTPS destinations."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
from pydantic import BaseModel, ConfigDict, Field

from bandpap.discovery import (
    DiscoveryDocument,
    OAuthMetadata,
    validate_domain,
    validate_https_url,
    validate_issuer,
    verify_discovery_binding,
)


class DiscoveryFailure(ValueError):
    """The discovery chain could not be validated within its limits."""


class FetchLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timeout: float = Field(default=10, gt=0, le=120, allow_inf_nan=False)
    max_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)
    max_redirects: int = Field(default=5, ge=0, le=10)


def public_address(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    if not parsed.is_global or parsed.is_multicast or parsed.is_reserved:
        return False
    if isinstance(parsed, ipaddress.IPv6Address):
        return (
            parsed in ipaddress.IPv6Network("2000::/3")
            and parsed.sixtofour is None
            and parsed.teredo is None
        )
    return True


class PublicStream(httpcore.AsyncNetworkStream):
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        async with asyncio.timeout(timeout):
            return await self.reader.read(max_bytes)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        async with asyncio.timeout(timeout):
            self.writer.write(buffer)
            await self.writer.drain()

    async def aclose(self) -> None:
        # Do not let a remote TLS close handshake extend the request deadline.
        self.writer.transport.abort()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> PublicStream:
        try:
            async with asyncio.timeout(timeout):
                await self.writer.start_tls(
                    ssl_context, server_hostname=server_hostname
                )
        except BaseException:
            self.writer.transport.abort()
            raise
        return self

    def get_extra_info(self, info: str) -> Any:
        return self.writer.get_extra_info(info)


class PublicNetwork(httpcore.AsyncNetworkBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[Any, ...]] | None = None,
    ) -> PublicStream:
        if local_address is not None or socket_options:
            raise DiscoveryFailure("Custom socket configuration is unsupported")
        async with asyncio.timeout(timeout):
            addresses = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
            if not addresses or any(
                not public_address(str(row[4][0])) for row in addresses
            ):
                raise DiscoveryFailure("Discovery destination is not a public address")
            last_error: OSError | None = None
            for family, _kind, _protocol, _name, address in addresses:
                try:
                    # Dial the validated numeric address; TLS still uses the original host.
                    reader, writer = await asyncio.open_connection(
                        str(address[0]),
                        port,
                        family=family,
                        flags=socket.AI_NUMERICHOST,
                    )
                    return PublicStream(reader, writer)
                except OSError as error:
                    last_error = error
            raise DiscoveryFailure(
                "Discovery destination is unreachable"
            ) from last_error

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class JsonResponse:
    url: str
    document: dict[str, Any]


class DiscoveryFetcher:
    """No ambient proxy, cookie jar, authentication or client-supplied headers."""

    def __init__(self, limits: FetchLimits | None = None) -> None:
        self.limits = limits or FetchLimits()

    async def get_json(self, url: str) -> JsonResponse:
        async with asyncio.timeout(self.limits.timeout):
            async with httpcore.AsyncConnectionPool(
                ssl_context=ssl.create_default_context(),
                network_backend=PublicNetwork(),
                max_connections=1,
                max_keepalive_connections=0,
            ) as pool:
                try:
                    return await self._fetch(pool, url)
                except (httpcore.ProtocolError, OSError, UnicodeError) as error:
                    raise DiscoveryFailure("Discovery transport failed") from error

    async def _fetch(
        self, pool: httpcore.AsyncConnectionPool, url: str
    ) -> JsonResponse:
        for hop in range(self.limits.max_redirects + 1):
            validate_https_url(url)
            async with pool.stream(
                "GET",
                url,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                headers = {key.lower(): value for key, value in response.headers}
                if response.status in {301, 302, 303, 307, 308}:
                    location = headers.get(b"location")
                    if location is None or hop == self.limits.max_redirects:
                        raise DiscoveryFailure(
                            "Invalid or excessive discovery redirects"
                        )
                    raw_location = location.decode("ascii")
                    if any(
                        ord(char) <= 32 or ord(char) == 127 for char in raw_location
                    ):
                        raise DiscoveryFailure("Invalid redirect location")
                    url = urljoin(url, raw_location)
                    continue
                if response.status != 200:
                    raise DiscoveryFailure(f"Discovery HTTP status {response.status}")
                if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                    raise DiscoveryFailure(
                        "Compressed discovery responses are unsupported"
                    )
                media_type = (
                    headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
                )
                if media_type != b"application/json" and not media_type.endswith(
                    b"+json"
                ):
                    raise DiscoveryFailure("Discovery response must be JSON")
                body = bytearray()
                async for chunk in response.aiter_stream():
                    body.extend(chunk)
                    if len(body) > self.limits.max_bytes:
                        raise DiscoveryFailure("Discovery response exceeds byte limit")
                try:
                    document = json.loads(body)
                except ValueError as error:
                    raise DiscoveryFailure("Invalid discovery JSON") from error
                if not isinstance(document, dict):
                    raise DiscoveryFailure("Discovery JSON must be an object")
                return JsonResponse(url=url, document=document)
        raise DiscoveryFailure("Excessive discovery redirects")


def metadata_url(issuer: str) -> str:
    validate_https_url(issuer)
    validate_issuer(issuer)
    parts = urlsplit(issuer)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            "/.well-known/oauth-authorization-server" + parts.path.rstrip("/"),
            "",
            "",
        )
    )


@dataclass(frozen=True)
class VerifiedDiscovery:
    document: DiscoveryDocument
    metadata: OAuthMetadata | None
    final_url: str


async def discover(
    domain: str, *, fetcher: DiscoveryFetcher | None = None
) -> VerifiedDiscovery:
    validate_domain(domain)
    fetcher = fetcher or DiscoveryFetcher()
    response = await fetcher.get_json(f"https://{domain}/.well-known/poppy.json")
    document = DiscoveryDocument.model_validate(response.document)
    if urlsplit(response.url).hostname != document.organization.domain.lower():
        raise DiscoveryFailure("Discovery organization does not match response host")
    metadata = None
    if document.auth is not None:
        oauth = await fetcher.get_json(metadata_url(document.auth.issuer))
        metadata = OAuthMetadata.model_validate(oauth.document)
    verify_discovery_binding(document, fetched_url=response.url, metadata=metadata)
    return VerifiedDiscovery(
        document=document, metadata=metadata, final_url=response.url
    )
