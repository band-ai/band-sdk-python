"""Author completions keep exact bodies and distinguish capacity from unknown delivery."""

from __future__ import annotations

import json

import httpx
import pytest

from band.contracts.author import AuthorCompletion, ContractAuthorClient
from band.contracts.errors import (
    ContractCapacityError,
    ContractProtocolError,
    ContractTransportError,
)
from band.contracts.wire import JsonObject
from tests.contracts.client.kit import RoomPeer


class TestCompletion:
    async def test_completion_retries_exact_bytes_after_unknown_ack(self) -> None:
        received: list[bytes] = []

        def peer(request: httpx.Request) -> httpx.Response:
            received.append(request.content)
            if len(received) == 1:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(
                200, json={"id": "job", "attempt": 1, "status": "ready"}
            )

        artifact: JsonObject = {"source": "hello", "derivation": [], "unresolved": []}
        completion = AuthorCompletion.success("job", attempt=1, result=artifact)
        artifact["source"] = "edited"
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(peer)) as http,
            ContractAuthorClient(RoomPeer().connection(), http=http) as client,
        ):
            with pytest.raises(ContractTransportError):
                await client.complete(completion)
            result = await client.complete(completion)
        assert result["status"] == "ready"
        assert (
            received[0]
            == received[1]
            == json.dumps(
                {
                    "attempt": 1,
                    "result": {"source": "hello", "derivation": [], "unresolved": []},
                }
            ).encode()
        )

    def test_production_serializer_rejects_expanded_unicode_before_delivery(
        self,
    ) -> None:
        with pytest.raises(ContractCapacityError, match="request"):
            AuthorCompletion.success("job", attempt=2, result={"source": "א" * 180000})

    async def test_wrong_job_ack_is_never_reported_as_completion(self) -> None:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"id": "different", "attempt": 0, "status": "ready"}
                    )
                )
            ) as http,
            ContractAuthorClient(RoomPeer().connection(), http=http) as client,
        ):
            with pytest.raises(ContractProtocolError):
                await client.complete(
                    AuthorCompletion.failure(
                        "job", attempt=0, failure="bounded", held=True
                    )
                )
