"""Reviewed Band wire examples; structure checks do not simulate authorization."""

from __future__ import annotations

import copy
from typing import Any

ROOM = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
USER = "User:33333333-3333-4333-8333-333333333333"
AGENT = "Agent:44444444-4444-4444-8444-444444444444"
BINDINGS = {"requester": USER, "assistant": AGENT}


def create_request(source: dict[str, Any]) -> dict[str, Any]:
    bindings = dict(BINDINGS)
    if source.get("profile") == "band-contract/file-source/1":
        bindings["requester"] = USER.replace("User:", "Agent:")
    return {
        "id": SESSION,
        "source": copy.deepcopy(source),
        "config": {
            "bindings": bindings,
            "inactivity": "accepted",
            "max_messages": 100,
            "max_bytes": 16384,
        },
    }


def wire_examples(source: dict[str, Any]) -> dict[str, Any]:
    examples = []

    def add(identifier: str, schema: str, valid: bool, value: Any) -> None:
        examples.append(
            {"id": identifier, "schema": schema, "valid": valid, "value": value}
        )

    create = create_request(source)
    add("create", "band_create_request", True, create)
    for identifier, path, value in [
        ("upper_uuid", ("id",), SESSION.replace("2", "A")),
        ("malformed_uuid", ("id",), "-" * 36),
        ("caller_actor", ("actor",), USER),
        ("caller_clock", ("config", "now"), 0),
        ("untyped_actor", ("config", "bindings", "assistant"), "worker"),
        ("malformed_actor", ("config", "bindings", "assistant"), "Agent:" + "-" * 36),
        # The profile's own valid requester plus a newline: the prefix matches, so only the end
        # anchor (and the fixed 42-character length in v2) can reject it.
        (
            "actor_newline",
            ("config", "bindings", "requester"),
            create["config"]["bindings"]["requester"] + "\n",
        ),
        ("actor_case", ("config", "bindings", "assistant"), AGENT.lower()),
        ("messages_zero", ("config", "max_messages"), 0),
        ("messages_over", ("config", "max_messages"), 10001),
        ("bytes_zero", ("config", "max_bytes"), 0),
        ("bytes_over", ("config", "max_bytes"), 65537),
        ("unknown_policy", ("config", "inactivity"), "caller"),
    ]:
        changed = copy.deepcopy(create)
        parent = changed
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
        add("create_" + identifier, "band_create_request", False, changed)
    missing = copy.deepcopy(create)
    del missing["config"]["max_bytes"]
    add("create_missing_bytes", "band_create_request", False, missing)
    if source.get("profile") == "band-contract/file-source/1":
        human = copy.deepcopy(create)
        human["config"]["bindings"]["requester"] = USER
        add("create_human_binding", "band_create_request", False, human)
    add("view", "band_view_request", True, {})
    add("view_actor", "band_view_request", False, {"actor": USER})
    add("view_clock", "band_view_request", False, {"now": 0})
    message = {
        "id": "q",
        "session": SESSION,
        "kind": "question",
        "payload": {"text": "ping"},
        "expected_version": 0,
    }
    add("proposal", "band_envelope", True, message)
    add("proposal_actor", "band_envelope", False, {**message, "actor": USER})
    add("proposal_clock", "band_envelope", False, {**message, "now": 0})
    add("proposal_session", "band_envelope", False, {**message, "session": "s"})
    add("proposal_version", "band_envelope", False, {**message, "expected_version": -1})
    for identifier, valid, value in [
        ("inbox", True, {"after": 0, "limit": 128}),
        ("inbox_negative", False, {"after": -1, "limit": 1}),
        ("inbox_empty_limit", False, {"after": 0, "limit": 0}),
        ("inbox_over_limit", False, {"after": 0, "limit": 129}),
        ("inbox_actor", False, {"after": 0, "limit": 1, "actor": AGENT}),
    ]:
        add(identifier, "inbox_request", valid, value)
    # Illustrative digests establish shape only; actual compiled identities are in the engine case.
    identity = {
        "profile": "contract-room/http/1",
        "session": SESSION,
        "actor": AGENT,
        "identity": {"definition": "a" * 64, "source": "b" * 64, "ready": True},
        "idempotent": True,
        "inbox_profile": "contract-room/inbox/1",
    }
    add("identity", "band_http_identity", True, identity)
    add("identity_actor", "band_http_identity", False, {**identity, "actor": "worker"})
    add("identity_session", "band_http_identity", False, {**identity, "session": "s"})
    add(
        "identity_dedup", "band_http_identity", False, {**identity, "idempotent": False}
    )
    add(
        "identity_inbox",
        "band_http_identity",
        False,
        {key: value for key, value in identity.items() if key != "inbox_profile"},
    )
    return {
        "profile": "band-contract/band-wire-examples/1",
        "provenance": "Independent structural expectations. No HTTP, membership, database or deployment qualification.",
        "examples": examples,
    }
