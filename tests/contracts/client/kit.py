"""Bounded native authority peer for public-client tests."""

from __future__ import annotations

import json

import httpx
from pydantic import JsonValue, SecretStr

from band.contracts.models import ContractConnection
from band.contracts.wire import JsonObject


def hydrated_rules(observation: JsonObject) -> list[JsonValue]:
    view = observation["view"]
    assert isinstance(view, dict)
    definition = view["definition"]
    assert isinstance(definition, dict)
    rules = definition["rules"]
    assert isinstance(rules, list)
    return rules


class RoomPeer:
    def __init__(self) -> None:
        self.actor = "Agent:participant"
        self.session = "session"
        self.identity = {"source": "pinned", "profile": "band/1"}
        self.requests: list[httpx.Request] = []
        self.proposals: list[bytes] = []
        self.lose_next_ack = False
        self.reject = False
        self.accepted: dict[str, dict] = {}
        self.definition_actor: str | None = None
        self.inbox_head: JsonObject | None = {"sequence": 2, "hash": "native"}

    def connection(self) -> ContractConnection:
        return ContractConnection(
            url="https://platform.test", credential=SecretStr("a" * 32)
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        match request.url.path.rsplit("/v1/", 1)[-1]:
            case "identity":
                body = {
                    "profile": "contract-room/http/1",
                    "idempotent": True,
                    "actor": self.actor,
                    "session": self.session,
                    "identity": self.identity,
                    "inbox_profile": "contract-room/inbox/1",
                }
            case "definition":
                body = {
                    "profile": "contract-room/definition/1",
                    "actor": self.definition_actor or self.actor,
                    "session": self.session,
                    "identity": self.identity,
                    "definition": {"role": "participant", "rules": ["pinned"]},
                }
            case "view":
                body = {"view": {"version": 1}, "checkpoint": {"version": 1}}
            case "proposals":
                self.proposals.append(request.content)
                proposal = json.loads(request.content)
                original = self.accepted.get(proposal["id"])
                body = {
                    "id": proposal["id"],
                    "status": "rejected" if self.reject else "accepted",
                    "session": self.session,
                    "actor": self.actor,
                    "version": 2,
                }
                if self.reject:
                    body.update(
                        reason="not_an_opening",
                        detail={"kind": proposal["kind"], "opening_kinds": ["assign"]},
                    )
                else:
                    self.accepted[proposal["id"]] = body
                if original is not None:
                    body = {"status": "duplicate", "original": original}
                if self.lose_next_ack:
                    self.lose_next_ack = False
                    raise httpx.ReadError("response lost", request=request)
            case "inbox":
                if json.loads(request.content)["after"] > 2:
                    return httpx.Response(
                        409, json={"code": "cursor_ahead", "error": "ahead"}
                    )
                body = {
                    "cursor": 2,
                    "events": [{"id": "acceptance", "kind": "question"}],
                }
                if self.inbox_head is not None:
                    body["head"] = self.inbox_head
            case _:
                return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json=body)
