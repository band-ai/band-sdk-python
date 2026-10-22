"""Independent current-profile assertions for semantics intentionally removed by D-3/D-6."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Sequence
from typing import Any

from contracts.spec.tools.vectorinputs import file_source, source, trusted_policy


def cases() -> list[dict[str, Any]]:
    rows = []

    def add(
        name: str,
        requests: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        requirements: list[str],
        schema_assertions: Sequence[dict[str, Any]] = (),
    ) -> None:
        row = {
            "profile": "band-contract/conformance-case/1",
            "id": "v2-" + name,
            "requirements": requirements,
            "requests": requests,
            "assertions": assertions,
        }
        if schema_assertions:
            row["source_schema_assertions"] = list(schema_assertions)
        rows.append(row)

    def expected(index: int, path: str, value: Any) -> dict[str, Any]:
        return {"response": index, "pointer": path, "equals": value}

    def compile(raw: str, **fields: Any) -> dict[str, Any]:
        return {"op": "compile", "handle": "c", "source": file_source(raw, **fields)}

    raw = source("explicit-infinity")["content"]
    body = raw.split("---\n", 2)[2]
    standard = (
        '---\nname: explicit-infinity\ndescription: Current standard package.\nmetadata:\n  band-engine: "0.2.0-lab.1"\n  band-roles: "a,b"\n  band-contract: "protocol.band"\n  band-contract-sha256: "'
        + hashlib.sha256(body.encode()).hexdigest()
        + '"\n---\nPackage guidance.\n'
    )
    standard_source = file_source(
        standard, format="standard", body=body, package_name="explicit-infinity"
    )
    corrupt = copy.deepcopy(standard_source)
    corrupt["body"] += "\n"
    add(
        "frontmatter-and-standard-discovery",
        [
            compile(raw),
            {"op": "compile", "handle": "std", "source": standard_source},
            {"op": "compile", "handle": "bad", "source": corrupt},
        ],
        [
            expected(0, "/ok/engine", "0.2.0-lab.1"),
            expected(1, "/ok/engine", "0.2.0-lab.1"),
            expected(1, "/ok/roles", ["a", "b"]),
            expected(2, "/error", "digest_mismatch"),
        ],
        ["DSL-001", "DSL-003", "DSL-008"],
    )
    for name, old, new, code in [
        (
            "reject-activity",
            "event a -> b: ping()",
            "event a -> b: ping() activity",
            "syntax",
        ),
        (
            "reject-due-all",
            "event a -> b: ping()",
            "due all of a with ping in 1s => expired",
            "syntax",
        ),
        (
            "reject-due-any",
            "event a -> b: ping()",
            "due any of a with ping in 1s => expired",
            "syntax",
        ),
        ("reject-plus-type", "type ping", "+TYPE ping", "syntax"),
    ]:
        assert old in raw
        add(
            name,
            [compile(raw.replace(old, new))],
            [expected(0, "/error/code", code)],
            ["DSL-004", "ENG-008", "ENG-010"],
        )
    # Header field errors take the compiler-error shape at the field's file line (Ada, 10-14).
    bad_header = raw.replace("roles: [a, b]", "inactivity-timeout: 5m\nroles: [a, b]")
    add(
        "reject-idle-header",
        [compile(bad_header)],
        [expected(0, "/error/code", "unknown_field"), expected(0, "/error/line", 5)],
        ["DSL-001", "ENG-010"],
    )
    native = "+SKILL explicit-infinity\n+SUMMARY Removed native header.\n+ENGINE 0.2.0-lab.1\n+ROLES a, b\n+BODY\ncontract @infinity => expired\ntype ping\nstate new\n  a -> b: ping() => done\nend done\n"
    # The source schema no longer admits native (Rowan c0ddad1); the reducer still answers
    # frontmatter_required (Ada's set O revise, 2026-10-14), so the schema oracle declares it invalid.
    add(
        "reject-native-header",
        [compile(native, format="native")],
        [expected(0, "/error", "frontmatter_required")],
        ["DSL-001"],
        [{"request": 0, "valid": False}],
    )
    bad_mentions = raw.replace("mentions 1", "mention_budget 1")
    add(
        "reject-session-mention-budget",
        [compile(bad_mentions)],
        [expected(0, "/error/code", "syntax")],
        ["ENG-013"],
    )
    human = trusted_policy(
        {
            "a": "User:33333333-3333-4333-8333-333333333333",
            "b": "Agent:44444444-4444-4444-8444-444444444444",
        }
    )
    human["principals"]["User:33333333-3333-4333-8333-333333333333"]["kind"] = "human"
    add(
        "human-binding-rejected",
        [
            compile(raw),
            {
                "op": "new",
                "contract": "c",
                "handle": "s",
                "config": {
                    "session": "s",
                    "bindings": {
                        "a": "User:33333333-3333-4333-8333-333333333333",
                        "b": "Agent:44444444-4444-4444-8444-444444444444",
                    },
                    "now": 0,
                    "max_messages": 100,
                    "max_bytes": 16384,
                    "room_policy": human,
                },
            },
        ],
        [expected(1, "/error", "agent_binding_required")],
        ["BAND-001"],
    )
    text = raw.replace("type ping", "type ping\n  text: text(bytes: 4, graphemes: 1)")
    start = [
        compile(text),
        {
            "op": "new",
            "contract": "c",
            "handle": "s",
            "config": {
                "session": "s",
                "bindings": {"a": "a", "b": "b"},
                "now": 0,
                "max_messages": 100,
                "max_bytes": 16384,
            },
        },
    ]

    def ping(identifier: str, value: Any) -> dict[str, Any]:
        return {
            "op": "step",
            "handle": "s",
            "actor": "a",
            "now": 0,
            "message": {
                "id": identifier,
                "session": "s",
                "kind": "ping",
                "payload": {"text": value},
            },
        }

    add(
        "utf8-and-grapheme-boundaries",
        start
        + [
            ping("astral", "🚀"),
            ping("byte-over", "🚀\u0301"),
            ping("two-graphemes", "ab"),
        ],
        [
            expected(2, "/decision/status", "accepted"),
            expected(3, "/decision/reason", "payload"),
            expected(4, "/decision/reason", "payload"),
            expected(4, "/state/state/version", 1),
        ],
        ["ENG-004"],
    )
    add(
        "reject-grapheme-only",
        [compile(text.replace("text(bytes: 4, graphemes: 1)", "text(graphemes: 1)"))],
        [expected(0, "/error/code", "type")],
        ["ENG-004"],
    )
    # More delivered mentions than the old session cap remain legal; only each message is capped.
    # Twenty-three nine-recipient deliveries cross the former 200-mention cap
    # without a quadratic 201-snapshot fixture. Every message obeys its own cap.
    names = list("abcdefghij")
    many = raw.replace("roles: [a, b]", "roles: [" + ", ".join(names) + "]").replace(
        "mentions 1", "mentions 9"
    )
    many = many.replace(
        "event a -> b: ping()", "event a -> {" + ",".join(names[1:]) + "}: ping()"
    )
    messages = [
        {
            "op": "step",
            "handle": "s",
            "actor": "a",
            "now": i,
            "message": {
                "id": "ping-" + str(i),
                "session": "s",
                "kind": "ping",
                "payload": {},
            },
        }
        for i in range(23)
    ]
    cfg = {
        "session": "s",
        "bindings": {name: name for name in names},
        "now": 0,
        "max_messages": 30,
        "max_bytes": 16384,
    }
    add(
        "mentions-not-cumulative",
        [compile(many), {"op": "new", "contract": "c", "handle": "s", "config": cfg}]
        + messages,
        [
            expected(24, "/decision/status", "accepted"),
            expected(24, "/state/state/mentions_used", 207),
        ],
        ["ENG-013"],
    )
    return rows
