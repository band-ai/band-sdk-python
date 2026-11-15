"""HTTP fixtures for the released generated Band client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from band.client.rest import AsyncRestClient


class BandHTTP:
    def __init__(self) -> None:
        self.proxy = "target"
        self.recipient = "muse"
        self.revoked = False
        self.revoke_on_identity = False
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            if self.revoke_on_identity:
                self.revoked = True
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": self.proxy,
                        "handle": "owner/target",
                        "name": "Target",
                        "owner_uuid": "owner",
                        "feature_flags": {},
                        "inserted_at": "2026-10-16T20:00:00Z",
                        "updated_at": "2026-10-16T20:00:00Z",
                    }
                },
            )
        return httpx.Response(
            201,
            json={
                "data": {
                    "id": "remote",
                    "success": True,
                    "recipients": [
                        {
                            "id": self.recipient,
                            "name": "Muse",
                            "handle": "owner/muse",
                            "type": "Agent",
                        }
                    ],
                }
            },
        )

    async def authorize(self, delivery: Any) -> None:
        if self.revoked:
            raise PermissionError("revoked")

    def client(self) -> AsyncRestClient:
        return AsyncRestClient(
            base_url="https://band.example",
            api_key="private-key",
            httpx_client=httpx.AsyncClient(
                transport=httpx.MockTransport(self.handle), follow_redirects=False
            ),
        )

    def posts(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.method == "POST"
        ]
