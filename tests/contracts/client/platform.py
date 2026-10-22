"""Serve the native platform's identity and full agent-view HTTP routes."""

from __future__ import annotations

import hashlib
import json

import httpx
from pydantic import JsonValue, SecretStr

from band.contracts.models import ContractConnection
from band.contracts.wire import JsonObject


class PlatformRoomPeer:
    def __init__(self) -> None:
        self.room = "9abbc918-d8e2-44f0-bc5f-7992a12f7636"
        self.session = "775bcafe-8ec0-4202-989e-980ec489c347"
        self.actor = "Agent:7386ba04-2699-44bd-bbc3-8747df269b2b"
        self.path = f"/api/v1/agent/chats/{self.room}/contracts/{self.session}"
        self.requests: list[httpx.Request] = []
        self.inbox: JsonObject = {"cursor": 7, "events": []}
        self.identity: JsonObject = {
            "profile": "contract-room/http/1",
            "idempotent": True,
            "actor": self.actor,
            "session": self.session,
            "identity": {"definition": "d" * 64, "source": "a" * 64, "ready": True},
            "inbox_profile": "contract-room/inbox/1",
        }
        self.definition: JsonObject = {
            "contract": {
                "name": "participant-protocol",
                "engine": "0.2.0-lab.1",
                "digest": "d" * 64,
                "source_digest": "a" * 64,
                "meta": {"doc": None, "line": 1},
            },
            "role": {
                "name": "participant",
                "meta": {"doc": "Answer requests", "line": 8},
            },
            "types": {"answer": {"name": "answer", "fields": {}}},
            "states": {},
            "terminals": {},
            "any_rules": [],
            "any_meta": {"doc": None, "line": 1},
            "room_rules": [],
            "room_meta": {"doc": None, "line": 1},
        }
        checkpoint: JsonObject = {
            "profile": "band-contract/state/2",
            "definition": "d" * 64,
            "source": "a" * 64,
            "state": {
                "id": self.session,
                "bindings": {"participant": self.actor},
                "now": 0,
                "state": "new",
                "status": "active",
                "outcome": None,
                "version": 0,
                "facts": {},
                "receipts": {},
                "history": [],
                "obligations": [],
            },
        }
        self.observation: JsonObject = {
            "profile": "band-contract/agent-view/1",
            "actor": self.actor,
            "now": 0,
            "checkpoint": checkpoint,
            "checkpoint_hash": hashlib.sha256(
                json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "view": {
                "id": self.session,
                "state": "new",
                "status": "active",
                "outcome": None,
                "version": 0,
                "definition": self.definition,
                "legal": [],
                "facts": {},
                "obligations": [],
                "max_at": 1800000,
                "idle_at": 1800000,
                "mentions_used": 0,
            },
        }

    def connection(self) -> ContractConnection:
        return ContractConnection(
            url="https://platform.test", credential=SecretStr("a" * 32)
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def replace(self, path: tuple[str, ...], value: JsonValue) -> None:
        parent = self.observation
        for field in path[:-1]:
            child = parent[field]
            assert isinstance(child, dict)
            parent = child
        parent[path[-1]] = value

    @property
    def routes(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "a" * 32
        self.requests.append(request)
        if request.method == "GET" and request.url.path == self.path + "/v1/identity":
            return httpx.Response(200, json=self.identity)
        if request.method == "POST" and request.url.path == self.path + "/v1/view":
            assert json.loads(request.content) == {}
            return httpx.Response(200, json=self.observation)
        if request.method == "POST" and request.url.path == self.path + "/v1/inbox":
            assert json.loads(request.content) == {"after": 3, "limit": 5}
            return httpx.Response(200, json=self.inbox)
        return httpx.Response(404, json={"error": "not_found"})
