"""D-9 assertions authored independently of Wire; current file-source syntax only."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

PROFILE = "band-contract/carrier/1"
HTTP = {"profile": PROFILE, "name": "http/1", "fields": {"session": "path:id"}}
EMAIL = {
    "profile": PROFILE,
    "name": "email/1",
    "fields": {
        "id": "header:Message-ID",
        "session": "header:X-Band-Session",
        "kind": "header:X-Band-Kind",
        "expected_version": "header:X-Band-Version",
    },
}
MESSAGE = {
    "id": "m",
    "session": "s",
    "kind": "ping",
    "payload": {},
    "expected_version": 0,
}
RAW = """---
name: carrier-retry
description: Retry the same normalized proposal across transport bindings.
engine: 0.2.0-lab.1
roles: [sender, receiver]
---
contract @infinity => expired
type ping
state new
  sender -> receiver: ping() => done
end done
"""


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def options(
    carrier: dict[str, Any] = EMAIL, transport: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "carrier": copy.deepcopy(carrier),
        "transport": transport
        if transport is not None
        else {
            EMAIL["fields"][key]: value
            for key, value in MESSAGE.items()
            if key != "payload"
        },
    }


def cases() -> list[dict[str, Any]]:
    result = []

    def add(
        name: str,
        requests: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        requirements: list[str] | None = None,
    ) -> None:
        result.append(
            {
                "profile": "band-contract/conformance-case/1",
                "id": "carrier-" + name,
                "requirements": requirements or ["ENG-002"],
                "requests": requests,
                "assertions": assertions,
            }
        )

    def assertion(index: int, pointer: str, value: Any) -> dict[str, Any]:
        return {"response": index, "pointer": pointer, "equals": value}

    def decode(
        name: str, body: str | dict[str, Any], opts: dict[str, Any], expected: Any
    ) -> None:
        request = {
            "op": "wire",
            "bytes": body if isinstance(body, str) else encoded(body),
            **opts,
        }
        add(name, [request], [assertion(0, "", expected)])

    def encode(
        name: str, message: dict[str, Any], opts: dict[str, Any], expected: Any
    ) -> None:
        add(
            name,
            [{"op": "wire_encode", "message": message, **opts}],
            [assertion(0, "", expected)],
        )

    http = options(HTTP, {"path:id": "s"})
    email = options()
    http_body = {k: v for k, v in MESSAGE.items() if k != "session"}
    decode("http-path", http_body, http, {"ok": MESSAGE})
    decode("email-headers", {"payload": {}}, email, {"ok": MESSAGE})
    decode("equal-body-and-headers", MESSAGE, email, {"ok": MESSAGE})
    decode(
        "body-defaults",
        MESSAGE,
        options({"profile": PROFILE, "name": "body/1", "fields": {}}, {}),
        {"ok": MESSAGE},
    )
    decode(
        "mapped-source-absent-fallback", MESSAGE, options(EMAIL, {}), {"ok": MESSAGE}
    )
    minimal = {"id": "m", "kind": "ping", "payload": {}}
    decode("optional-absent", minimal, options(EMAIL, {}), {"ok": minimal})
    for key in ("id", "kind", "payload"):
        decode(
            "required-" + key,
            {k: v for k, v in minimal.items() if k != key},
            options(EMAIL, {}),
            {"error": "envelope"},
        )
    for key in ("id", "session", "kind", "expected_version"):
        changed = {**MESSAGE, key: 1 if key == "expected_version" else "different"}
        decode("conflict-" + key, changed, email, {"error": "transport_conflict"})
        bad = copy.deepcopy(email)
        bad["transport"][EMAIL["fields"][key]] = (
            1 if key == "expected_version" else "different"
        )
        encode("encode-conflict-" + key, MESSAGE, bad, {"error": "transport_conflict"})
    encode("encode-http-omit-only-supplied", MESSAGE, http, {"ok": encoded(http_body)})
    encode("encode-email-omit-supplied", MESSAGE, email, {"ok": '{"payload":{}}'})
    encode(
        "encode-fallback-keeps-body",
        MESSAGE,
        options(EMAIL, {}),
        {"ok": encoded(MESSAGE)},
    )
    encode(
        "encode-optional-absent", minimal, options(EMAIL, {}), {"ok": encoded(minimal)}
    )
    absent_optional = options(EMAIL, {EMAIL["fields"]["session"]: "s"})
    encode(
        "encode-cannot-introduce-optional",
        minimal,
        absent_optional,
        {"error": "transport_conflict"},
    )
    for key in ("actor", "clock", "payload", "unknown"):
        bad = options(
            {"profile": PROFILE, "name": "forbidden/1", "fields": {key: "header:X"}}, {}
        )
        decode("forbidden-map-" + key, MESSAGE, bad, {"error": "transport_fields"})
        encode(
            "encode-forbidden-map-" + key, MESSAGE, bad, {"error": "transport_fields"}
        )
    malformed = [
        None,
        [],
        {"profile": "band-contract/carrier/99", "name": "x/1", "fields": {}},
        {"profile": PROFILE, "fields": {}},
        {"profile": PROFILE, "name": "x/1", "fields": []},
    ]
    for index, carrier in enumerate(malformed):
        bad = {**email, "carrier": carrier}
        decode("invalid-map-" + str(index), MESSAGE, bad, {"error": "transport_fields"})
    decode(
        "undeclared-supplied-source",
        MESSAGE,
        {**email, "transport": {"header:Undeclared": "m"}},
        {"error": "transport_fields"},
    )
    shared = {
        "profile": PROFILE,
        "name": "shared/1",
        "fields": {"id": "header:X", "kind": "header:X"},
    }
    decode(
        "shared-source-validates-each-field",
        {"payload": {}},
        options(shared, {"header:X": "m"}),
        {"ok": {"id": "m", "kind": "m", "payload": {}}},
    )
    explicit_body = {"profile": PROFILE, "name": "body/1", "fields": {"id": "body"}}
    decode(
        "body-token-is-not-external",
        MESSAGE,
        options(explicit_body, {"body": "m"}),
        {"error": "transport_fields"},
    )
    for key, value in [
        ("id", ""),
        ("id", 1),
        ("kind", ""),
        ("kind", True),
        ("session", 1),
    ]:
        values = {**email["transport"], EMAIL["fields"][key]: value}
        decode(
            "external-type-" + key + "-" + str(value),
            {"payload": {}},
            {**email, "transport": values},
            {"error": "envelope"},
        )
    for index, value in enumerate((0, 9_007_199_254_740_991, -1, "0", True, None)):
        opts = {
            **email,
            "transport": {
                **email["transport"],
                EMAIL["fields"]["expected_version"]: value,
            },
        }
        decode(
            "version-" + str(index),
            {"payload": {}},
            opts,
            {"ok": {**MESSAGE, "expected_version": value}}
            if index < 2
            else {"error": "envelope"},
        )
    for name, body, error in [
        ("duplicate-json", '{"payload":{},"payload":{}}', "duplicate_key"),
        ("float-json", '{"payload":{"x":1.0}}', "unsupported_number"),
        ("unsafe-json", '{"payload":{"x":9007199254740992}}', "unsupported_number"),
        ("trailing-json", '{"payload":{}} trailing', "json"),
        ("actor-in-body", {"payload": {}, "actor": "spoof"}, "envelope"),
    ]:
        decode(name, body, email, {"error": error})
    decode(
        "raw-size",
        {"payload": {}},
        {**email, "max_bytes": 1},
        {"error": "message_size"},
    )
    decode(
        "normalized-size",
        {"payload": {}},
        {**email, "max_bytes": 32},
        {"error": "message_size"},
    )
    decode(
        "legacy-id-session",
        {"kind": "ping", "payload": {}},
        {"transport": {"id": "m", "session": "s"}},
        {"ok": {**minimal, "session": "s"}},
    )

    source = {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": RAW,
        "sha256": hashlib.sha256(RAW.encode()).hexdigest(),
    }
    requests = [
        {"op": "compile", "handle": "c", "source": source},
        {
            "op": "new",
            "contract": "c",
            "handle": "s",
            "config": {
                "session": "s",
                "bindings": {"sender": "alice", "receiver": "bob"},
                "now": 0,
                "max_messages": 4,
            },
        },
        {
            "op": "wire_step",
            "handle": "s",
            "actor": "alice",
            "now": 0,
            "bytes": encoded(http_body),
            **http,
        },
        {
            "op": "wire_step",
            "handle": "s",
            "actor": "alice",
            "now": 1,
            "bytes": '{"payload":{}}',
            **email,
        },
        {
            "op": "wire_step",
            "handle": "s",
            "actor": "bob",
            "now": 2,
            "bytes": '{"payload":{}}',
            **email,
        },
        {
            "op": "wire_step",
            "handle": "s",
            "actor": "alice",
            "now": 3,
            "bytes": '{"payload":{}}',
            **email,
            "transport": {**email["transport"], EMAIL["fields"]["expected_version"]: 1},
        },
    ]
    add(
        "cross-carrier-retry",
        requests,
        [
            assertion(2, "/decision/status", "accepted"),
            assertion(3, "/decision/status", "duplicate"),
            assertion(3, "/decision/original/version", 1),
            assertion(3, "/state/state/version", 1),
            assertion(4, "/decision/reason", "id_conflict"),
            assertion(5, "/decision/reason", "id_conflict"),
        ],
        ["ENG-002", "ENG-005"],
    )
    return result
