"""Authenticated protocol transport with no redirects or ambient credentials."""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpcore

from bandpap.discovery import validate_https_url
from bandpap.fetch import FetchLimits, PublicNetwork


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = field(default=b"", repr=False)

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("Expected a protocol JSON object")
        return value

    def retry_delay(self, now: float) -> float:
        value = self.headers.get("retry-after", "1")
        if value.isdigit():
            return float(value)
        try:
            return max(
                0,
                (
                    parsedate_to_datetime(value) - datetime.fromtimestamp(now, UTC)
                ).total_seconds(),
            )
        except (ValueError, TypeError, OverflowError):
            return 1


class ProtocolError(ValueError):
    def __init__(self, response: Response) -> None:
        try:
            self.code = str(response.json().get("error", "protocol_error"))
        except ValueError:
            self.code = "protocol_error"
        if self.code == "protocol_error":
            match = re.search(
                r'\berror="([A-Za-z0-9_-]+)"',
                response.headers.get("www-authenticate", ""),
            )
            if match:
                self.code = match.group(1)
        super().__init__(self.code)
        self.response = response


class Transport:
    def __init__(self, limits: FetchLimits | None = None) -> None:
        self.limits = limits or FetchLimits(timeout=35)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        document: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        validate_https_url(url)
        fields = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            **(headers or {}),
        }
        if document is not None and form is not None:
            raise ValueError("Choose JSON or form body")
        body = b""
        if document is not None:
            body = json.dumps(document, allow_nan=False).encode()
            fields["Content-Type"] = "application/json"
        if form is not None:
            body = urlencode(form).encode()
            fields["Content-Type"] = "application/x-www-form-urlencoded"
        if len(body) > self.limits.max_bytes:
            raise ValueError("Request exceeds byte limit")
        async with asyncio.timeout(self.limits.timeout):
            async with (
                httpcore.AsyncConnectionPool(
                    ssl_context=ssl.create_default_context(),
                    network_backend=PublicNetwork(),
                    max_keepalive_connections=0,
                ) as pool,
                pool.stream(
                    method, url, headers=list(fields.items()), content=body
                ) as result,
            ):
                metadata = {
                    key.decode().lower(): value.decode()
                    for key, value in result.headers
                }
                if 300 <= result.status < 400:
                    raise ValueError("Authenticated redirects are forbidden")
                if metadata.get("content-encoding", "identity") != "identity":
                    raise ValueError("Compressed protocol response unsupported")
                content = bytearray()
                async for chunk in result.aiter_stream():
                    content.extend(chunk)
                    if len(content) > self.limits.max_bytes:
                        raise ValueError("Response exceeds byte limit")
                return Response(result.status, metadata, bytes(content))
