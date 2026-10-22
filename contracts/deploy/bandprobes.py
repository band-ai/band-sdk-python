"""Schema-checked access and immutable-create drills for the Band adapter."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

import jsonschema


def validate_response(
    value: Any, status: int, path: str, schema: dict[str, Any]
) -> None:
    if status >= 400:
        errors = {
            400: ["invalid_request", "invalid_json"],
            404: ["not_found"],
            409: ["id_conflict", "room_inactive"],
            422: ["invalid_source", "invalid_bindings"],
            503: ["authority_unavailable", "response_capacity"],
        }
        validator = {
            "type": "object",
            "properties": {"error": {"enum": errors.get(status, [])}},
            "required": ["error"],
            "additionalProperties": False,
        }
    else:
        name = (
            "projection"
            if path.endswith("/view")
            else "inbox_response"
            if path.endswith("/inbox")
            else "decision"
            if path.endswith("/proposals")
            else "band_http_identity"
        )
        validator = {**schema, "$ref": "#/$defs/" + name}
    jsonschema.Draft202012Validator(validator).validate(value)


def schema_checked_client(client: Any, schema: dict[str, Any]) -> Any:
    request = client._request

    def checked(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        value = request(method, path, *args, **kwargs)
        validate_response(value, 200, path, schema)
        return value

    client._request = checked
    return client


def probe_access_and_creation(
    http: Callable[..., Any],
    ports: Sequence[int],
    data: dict[str, Any],
    request: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, int]:
    denied, retries, conflicts = 0, 0, 0
    for port in ports:
        for surface, keys in (
            ("me", ("observer_key", "outsider_key")),
            ("agent", ("observer_agent_key", "outsider_agent_key")),
        ):
            base = f"/api/v1/{surface}/chats/{data['room']}/contracts/{data['session']}"
            for key in keys:
                for endpoint, body in (
                    ("identity", None),
                    ("view", {}),
                    ("inbox", {"after": 0, "limit": 100}),
                    (
                        "proposals",
                        {
                            "id": "denied",
                            "session": data["session"],
                            "kind": "question",
                            "payload": {"text": "private"},
                        },
                    ),
                ):
                    http(port, base + "/v1/" + endpoint, data[key], body, expected=404)
                    denied += 1
        base = f"/api/v1/agent/chats/{data['room']}/contracts"
        repeated = http(port, base, data["requester_key"], request, expected=201)
        if repeated != identity:
            raise AssertionError("Exact create retry changed the session identity")
        retries += 1
        changed = copy.deepcopy(request)
        changed["config"]["max_messages"] += 1
        http(port, base, data["requester_key"], changed, expected=409)
        other = f"/api/v1/agent/chats/{data['other_room']}/contracts"
        http(port, other, data["requester_key"], request, expected=409)
        conflicts += 2
    return {
        "denial_probes": denied,
        "identical_create_retries": retries,
        "immutable_create_conflicts": conflicts,
    }


def probe_invalid_requests(
    http: Callable[..., Any],
    ports: Sequence[int],
    data: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, int]:
    checks = 0
    for port in ports:
        base = f"/api/v1/agent/chats/{data['room']}/contracts"
        session = base + "/" + data["session"]
        http(
            port,
            session + "/v1/view",
            data["requester_key"],
            {"actor": "forged"},
            expected=400,
        )
        http(
            port,
            session + "/v1/inbox",
            data["requester_key"],
            {"after": -1, "limit": 100},
            expected=400,
        )
        changed = copy.deepcopy(request)
        changed["source"]["sha256"] = "0" * 64
        http(port, base, data["requester_key"], changed, expected=422)
        checks += 3
    return {"invalid_request_probes": checks}
