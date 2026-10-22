"""Bounded, nonredirecting transport rejects ambiguous protocol data."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from band.contracts.author import ContractAuthorClient
from band.contracts.errors import (
    ContractCapacityError,
    ContractHTTPError,
    ContractProtocolError,
)
from band.contracts.models import ContractConnection
from tests.contracts.client.kit import RoomPeer


class TestTransport:
    @pytest.mark.parametrize(
        "body", [b'{"job":null,"job":null}', b'{"job":NaN}', b"[]"]
    )
    async def test_claim_rejects_non_protocol_response(self, body: bytes) -> None:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=body)
                )
            ) as http,
            ContractAuthorClient(RoomPeer().connection(), http=http) as client,
        ):
            with pytest.raises(ContractProtocolError):
                await client.claim(id="stable")

    async def test_response_capacity_is_distinct_from_unknown_delivery(self) -> None:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b" " * 1048577)
                )
            ) as http,
            ContractAuthorClient(RoomPeer().connection(), http=http) as client,
        ):
            with pytest.raises(ContractCapacityError, match="response"):
                await client.claim(id="stable")

    async def test_redirect_is_not_followed_with_the_agent_credential(self) -> None:
        requested: list[str] = []

        def redirect(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                307,
                headers={"location": "https://other.test"},
                json={"error": "redirect"},
            )

        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(redirect), follow_redirects=True
            ) as http,
            ContractAuthorClient(RoomPeer().connection(), http=http) as client,
        ):
            with pytest.raises(ContractHTTPError) as error:
                await client.claim(id="stable")
        assert error.value.status == 307
        assert requested == [
            "https://platform.test/api/v1/agent/contracts/authoring/claim"
        ]

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.test",
            "http://localhost",
            "https://user:secret@example.test",
            "https://example.test?key=secret",
            "https://example.test#scope",
        ],
    )
    def test_invalid_authority_configuration_is_rejected(self, url: str) -> None:
        with pytest.raises(ValidationError):
            ContractConnection(url=url, credential=SecretStr("a" * 32))
