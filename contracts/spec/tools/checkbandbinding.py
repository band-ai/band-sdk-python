"""Static Band binding qualification targets, never hosted authorization proof.

Trusted-context descriptors in the fixture are test inputs, not API fields or
evidence that a principal was authenticated. No Band request is performed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from contracts.spec.tools.buildv2schemas import root as schema
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT

PATH = ROOT / "spec/conformance/band-binding-examples-v2.json"
PROFILE = "band-contract/band-binding-examples/2"
SESSION = "22222222-2222-4222-8222-222222222222"
USER = "User:33333333-3333-4333-8333-333333333333"
AGENT_A = "Agent:44444444-4444-4444-8444-444444444444"
AGENT_B = "Agent:55555555-5555-4555-8555-555555555555"
ADMIN = "Agent:66666666-6666-4666-8666-666666666666"
DEFINITION = "a" * 64
CONTENT = """---
name: band-binding-target
description: Agent-only binding qualification target.
engine: 0.2.0-lab.1
roles: [requester, assistant]
---
contract @infinity => expired
type notice
state new
  event requester -> assistant: notice()
end expired
"""


def creation_inputs() -> list[dict[str, Any]]:
    """Required attacks are independent of editable fixture expectations."""
    source = {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": CONTENT,
        "sha256": hashlib.sha256(CONTENT.encode()).hexdigest(),
    }
    request = {
        "id": SESSION,
        "source": source,
        "config": {
            "bindings": {"requester": AGENT_A, "assistant": AGENT_B},
            "max_messages": 100,
            "max_bytes": 16384,
        },
    }
    context = {
        "actor": USER,
        "room_active": True,
        "active": True,
        "blocked": False,
        "permission": "owner",
        "memberships": {
            actor: {"active": True, "blocked": False} for actor in (AGENT_A, AGENT_B)
        },
    }
    cases = []

    def add(
        identifier: str,
        operation: str = "create",
        ctx: dict[str, Any] | None = None,
        req: dict[str, Any] | None = None,
        retry: bool = False,
    ) -> None:
        current = copy.deepcopy(context)
        current.update(ctx or {})
        cases.append(
            {
                "id": identifier,
                "operation": operation,
                "exact_create_retry": retry,
                "context": current,
                "request": copy.deepcopy(request if req is None else req)
                if operation == "create"
                else {},
            }
        )

    add("human_owner_create")
    add("agent_admin_create", ctx={"actor": ADMIN, "permission": "admin"})
    add("human_admin_create", ctx={"permission": "admin"})
    add("bound_agent_owner_create", ctx={"actor": AGENT_A})
    add("participant_create", ctx={"actor": ADMIN, "permission": "participant"})
    for field, value in [("active", False), ("blocked", True), ("room_active", False)]:
        add("creator_" + field, ctx={field: value})
    add("exact_create_retry", retry=True)
    add("revoked_create_retry", ctx={"active": False}, retry=True)
    for identifier, bindings in [
        ("human_binding", {"requester": USER, "assistant": AGENT_B}),
        ("missing_binding", {"requester": AGENT_A}),
        (
            "extra_binding",
            {"requester": AGENT_A, "assistant": AGENT_B, "observer": ADMIN},
        ),
        ("duplicate_binding", {"requester": AGENT_A, "assistant": AGENT_A}),
    ]:
        changed = copy.deepcopy(request)
        changed["config"]["bindings"] = bindings
        add(identifier, req=changed)
    for field, value in [("active", False), ("blocked", True)]:
        memberships = copy.deepcopy(context["memberships"])
        memberships[AGENT_B][field] = value
        add("binding_" + field, ctx={"memberships": memberships})
    for operation in ("identity", "view", "proposals", "inbox"):
        add("human_" + operation, operation=operation)
        add(
            "unbound_agent_" + operation,
            operation=operation,
            ctx={"actor": ADMIN, "permission": "admin"},
        )
        add(
            "bound_agent_" + operation,
            operation=operation,
            ctx={"actor": AGENT_A, "permission": "participant"},
        )
        add(
            "removed_agent_" + operation,
            operation=operation,
            ctx={"actor": AGENT_A, "active": False},
        )
    return cases


def creation_decision(case: dict[str, Any]) -> dict[str, Any]:
    context = case["context"]
    authorized = context["active"] and not context["blocked"]
    if case["operation"] != "create":
        # Creator authority is not a role; a returned create identity grants no
        # ordinary identity/view/proposal/inbox access to an unbound principal.
        bound_agent = context["actor"] in (AGENT_A, AGENT_B)
        return {"http_status": 200 if authorized and bound_agent else 404}
    if (
        not authorized
        or not context["room_active"]
        or context["permission"] not in ("owner", "admin")
    ):
        return {"http_status": 404}
    request = case["request"]
    bindings = request["config"]["bindings"]
    members = context["memberships"]
    valid = (
        set(bindings) == {"requester", "assistant"}
        and len(set(bindings.values())) == 2
        and all(
            validator("band_actor").is_valid(actor)
            and members.get(actor, {}).get("active") is True
            and members.get(actor, {}).get("blocked") is False
            for actor in bindings.values()
        )
    )
    if not valid:
        return {"http_status": 422}
    validator("band_create_request").validate(request)
    return {
        "http_status": 201,
        "identity": {
            "profile": "contract-room/http/1",
            "session": request["id"],
            "actor": context["actor"],
            "identity": {
                "definition": DEFINITION,
                "source": request["source"]["sha256"],
                "ready": True,
            },
            "idempotent": True,
            "inbox_profile": "contract-room/inbox/1",
        },
    }


def validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/" + name})


def retention_inputs() -> list[dict[str, Any]]:
    """Internal archive predicates are targets, not a new public API route."""
    receipt = {
        "status": "accepted",
        "id": "archive-receipt-1",
        "session": SESSION,
        "version": 1,
    }
    base = {
        "unarchived_before": 256,
        "terminal": True,
        "unresolved_effects": False,
        "archive_verified": True,
        "archive_committed": True,
        "receipts_reachable": True,
        "global_id_claim": True,
        "cursor_before": 41,
        "original_receipt": receipt,
    }
    cases = []

    def add(identifier: str, operation: str, changes: Any = None) -> None:
        context = copy.deepcopy(base)
        context.update(changes or {})
        cases.append({"id": identifier, "operation": operation, "context": context})

    add("full_new_create", "new_create")
    add("full_exact_create_retry", "exact_create_retry")
    add(
        "terminal_without_archive_still_full",
        "new_create",
        {"archive_verified": False, "archive_committed": False},
    )
    add("verified_terminal_archive", "archive")
    for identifier, changes in [
        ("active_archive", {"terminal": False}),
        ("unverified_archive", {"archive_verified": False}),
        ("uncommitted_archive", {"archive_committed": False}),
        ("unresolved_archive", {"unresolved_effects": True}),
        ("unreachable_receipts_archive", {"receipts_reachable": False}),
        ("lost_id_claim_archive", {"global_id_claim": False}),
    ]:
        add(identifier, "archive", changes)
    add("new_create_after_archive", "new_create", {"unarchived_before": 255})
    return cases


def retention_decision(case: dict[str, Any]) -> dict[str, Any]:
    context = case["context"]
    count = context["unarchived_before"]
    operation = case["operation"]
    if operation == "archive":
        release = (
            context["terminal"]
            and not context["unresolved_effects"]
            and context["archive_verified"]
            and context["archive_committed"]
            and context["receipts_reachable"]
            and context["global_id_claim"]
        )
        if not release:
            return {
                "slot_released": False,
                "unarchived_after": count,
                "history_unchanged": True,
            }
        return {
            "slot_released": True,
            "unarchived_after": count - 1,
            "receipts_reachable": True,
            "global_id_claim": True,
            "cursor_after": context["cursor_before"],
            "exact_retry": {
                "status": "duplicate",
                "original": context["original_receipt"],
            },
        }
    if operation == "exact_create_retry":
        # N1 separately pins current authorization and binding revalidation.
        return {"http_status": 201, "retryable": False, "unarchived_after": count}
    if count >= 256:
        return {
            "http_status": 409,
            "body": {"error": "room_session_limit"},
            "retryable": False,
            "unarchived_after": count,
        }
    return {"http_status": 201, "retryable": False, "unarchived_after": count + 1}


def check_targets(
    group: str,
    cases: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    decide: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    assert [case["id"] for case in cases] == [case["id"] for case in inputs], (
        group + " coverage differs"
    )
    for case, pinned in zip(cases, inputs):
        assert {
            key: value for key, value in case.items() if key != "expected"
        } == pinned, group + " inputs differ"
        assert case["expected"] == decide(pinned), (
            group + " decision differs: " + case["id"]
        )


def visibility_inputs() -> list[dict[str, Any]]:
    base = {
        "session": SESSION,
        "proposal_id": "private-canary-id:only-requester-assistant",
        "definition": DEFINITION,
        "payload": {"text": "private-canary-payload:do-not-share"},
        "documentation": "private-canary-doc:do-not-share",
    }
    cases = []
    for identifier, changes in [
        ("private_id_canary", {}),
        ("same_id_other_session", {"session": "77777777-7777-4777-8777-777777777777"}),
        ("exact_utf8_id", {"proposal_id": "private-canary-id:caf\u00e9|\u03bb"}),
        ("decomposed_utf8_id", {"proposal_id": "private-canary-id:cafe\u0301|\u03bb"}),
        ("definition_not_in_handle", {"definition": "b" * 64}),
    ]:
        cases.append({"id": identifier, "context": {**copy.deepcopy(base), **changes}})
    return cases


def visibility_decision(case: dict[str, Any]) -> dict[str, Any]:
    context = case["context"]
    validator("band_uuid").validate(context["session"])
    # No JSON encoding, normalization, trimming or definition digest participates
    # in this hash. IDs remain exact UTF-8 strings in the private reconciliation link.
    handle = hashlib.sha256(
        (context["session"] + "|" + context["proposal_id"]).encode("utf-8")
    ).hexdigest()
    metadata = {
        "session": context["session"],
        "receipt_handle": handle,
        "definition": context["definition"],
    }
    record = {"text": "Contract event accepted.", "contract": metadata}
    # Abstract shared projections, not a claimed Band chat/notification wire shape.
    return {
        "shared_history": [record],
        "notifications": [copy.deepcopy(record)],
        "private_link": {
            "session": context["session"],
            "proposal_id": context["proposal_id"],
        },
        "stable_retry_handle": handle,
    }


def check(fixture: dict[str, Any]) -> dict[str, Any]:
    assert fixture["profile"] == PROFILE, "Binding fixture profile differs"
    assert fixture["band_live_checked"] is False, (
        "Targets cannot claim live Band checks"
    )
    assert fixture["qualification"] == "not_assessed", (
        "Targets cannot claim qualification"
    )
    check_targets("creation", fixture["creation"], creation_inputs(), creation_decision)
    for case in fixture["creation"]:
        if case["expected"]["http_status"] == 201:
            validator("band_http_identity").validate(case["expected"]["identity"])
    check_targets(
        "retention", fixture["retention"], retention_inputs(), retention_decision
    )
    check_targets(
        "visibility", fixture["visibility"], visibility_inputs(), visibility_decision
    )
    for case in fixture["visibility"]:
        shared = json.dumps(
            {
                name: case["expected"][name]
                for name in ("shared_history", "notifications")
            },
            ensure_ascii=False,
        )
        for canary in (
            "private-canary-id",
            "private-canary-payload",
            "private-canary-doc",
        ):
            assert canary not in shared, "visibility canary leaked: " + canary
    return {
        "profile": PROFILE,
        "band_live_checked": False,
        "qualification": "not_assessed",
        "creation_targets": len(fixture["creation"]),
        "retention_targets": len(fixture["retention"]),
        "visibility_targets": len(fixture["visibility"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=PATH)
    args = parser.parse_args()
    sys.stdout.write(
        str(json.dumps(check(json.loads(args.fixture.read_text())), sort_keys=True))
        + "\n"
    )


if __name__ == "__main__":
    main()
