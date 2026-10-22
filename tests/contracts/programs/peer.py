"""Capture public program requests at the authenticated host boundary."""

from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from band.contracts.models import ContractConnection
from band.contracts.programs import ProgramScope
from band.contracts.wire import JsonObject


class ProgramPeer:
    def __init__(self) -> None:
        self.requests: list[JsonObject] = []
        self.scope = ProgramScope(
            room="room", session="session", actor="User:owner", context={}
        )
        self.connection = ContractConnection(
            url="http://127.0.0.1:8000",
            credential=SecretStr("h" * 32),
            authentication="bearer",
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/operations"
        assert request.headers["authorization"] == "Bearer " + "h" * 32
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "profile": "band-contract/program/1",
                "room": self.scope.room,
                "session": self.scope.session,
            },
        )
