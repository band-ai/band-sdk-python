"""Portable async HTTP transport with bounded responses and explicit ownership."""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Self

import httpx

from band.contracts.errors import (
    ContractCapacityError,
    ContractHTTPError,
    ContractInboxConflict,
    ContractTransportError,
)
from band.contracts.models import ContractConnection
from band.contracts.wire import MAX_WIRE_BYTES, JsonObject, decode_json

logger = logging.getLogger(__name__)


class ContractTransport:
    def __init__(
        self, connection: ContractConnection, *, http: httpx.AsyncClient | None = None
    ) -> None:
        self.connection = connection
        self._owned = http is None
        self._http = http or httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=connection.timeout
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        limit: int = MAX_WIRE_BYTES,
    ) -> JsonObject:
        credential = self.connection.credential.get_secret_value()
        headers = {"accept": "application/json", "content-type": "application/json"}
        if self.connection.authentication == "bearer":
            headers["authorization"] = f"Bearer {credential}"
        else:
            headers["x-api-key"] = credential
        try:
            async with (
                asyncio.timeout(self.connection.timeout),
                self._http.stream(
                    method,
                    self.connection.url + path,
                    content=body,
                    headers=headers,
                    timeout=self.connection.timeout,
                    follow_redirects=False,
                ) as response,
            ):
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > limit:
                        raise ContractCapacityError("response")
                    content.extend(chunk)
                result = self._decode_response(bytes(content))
                if response.status_code != 200:
                    code = result.get("code")
                    code = code if isinstance(code, str) and len(code) <= 100 else None
                    if (
                        response.status_code == 409
                        and path.endswith("/v1/inbox")
                        and code == "cursor_ahead"
                    ):
                        raise ContractInboxConflict(409, code)
                    raise ContractHTTPError(response.status_code, code)
                return result
        except (httpx.HTTPError, TimeoutError):
            logger.debug("Contract request ended without an authoritative response")
            raise ContractTransportError(
                "Contract delivery is unknown; retry only the same request identity and body"
            ) from None

    def _decode_response(self, content: bytes) -> JsonObject:
        return decode_json(content)

    async def aclose(self) -> None:
        if self._owned:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
