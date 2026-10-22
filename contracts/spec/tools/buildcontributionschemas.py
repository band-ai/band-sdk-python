"""Versioned contribution outcomes; earlier portable bundles remain immutable."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from contracts.spec.tools.buildparticipantschemas import root as base
from contracts.spec.tools.buildv2schemas import add_http_wire_schemas
from contracts.spec.tools.schemacommon import (
    array,
    const,
    enum,
    mapping,
    nullable,
    obj,
    ref,
)

root = copy.deepcopy(base)
root["$id"] = "https://band.ai/spec/contract/protocol/5"
root["title"] = "Multi-agent skills: room contribution outcomes"
D = root["$defs"]
D["card"]["properties"]["extensions"]["items"]["enum"].append("room.contributions/1")
for properties in [
    D["compiled"]["properties"],
    D["role_definition"]["properties"]["contract"]["properties"],
]:
    properties["contribution_profile"] = const("band-contract/contribution/1")
D["collection_definition"]["properties"]["profile"] = enum(
    "band-contract/collection/1", "band-contract/collection/2"
)


def text(limit: int) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": limit,
        "x-maxUtf8Bytes": limit,
    }


D["contribution_cause"] = obj(
    {"type": text(128), "reference": nullable(ref("id")), "message": text(2048)}
)
D["contribution_failure"] = obj(
    {
        "id": ref("id"),
        "origin": enum("member", "host"),
        **copy.deepcopy(D["contribution_cause"]["properties"]),
    }
)
D["collection_member"] = {
    "oneOf": [
        copy.deepcopy(D["collection_member"]),
        obj(
            {
                **copy.deepcopy(D["collection_member"]["properties"]),
                "failure": nullable(ref("contribution_failure")),
            }
        ),
    ]
}
D["contribution_report"] = obj(
    {
        "id": ref("id"),
        "session": ref("id"),
        "collection": ref("identifier"),
        "entry": ref("positive"),
        "member": ref("member_name"),
        "cause": ref("contribution_cause"),
    }
)
D["contribution_accepted"] = obj(
    {
        "status": const("contribution_stopped"),
        "id": ref("id"),
        "session": ref("id"),
        "collection": ref("identifier"),
        "entry": ref("positive"),
        "member": ref("member_name"),
        "origin": enum("member", "host"),
        "at": ref("uint"),
        "version": ref("positive"),
        "state": ref("identifier"),
        "outcome": nullable(ref("identifier")),
    }
)


# Like engine decisions, a contribution decision of an unchecked local session is marked
# (D-15); its stored receipt is not.
def unchecked_marker(schema: dict[str, Any]) -> dict[str, Any]:
    marked = copy.deepcopy(schema)
    marked["properties"]["policy"] = const("unchecked")
    return marked


D["contribution_rejection"] = copy.deepcopy(D["rejected"])
D["contribution_rejection"]["properties"].pop("detail", None)
D["contribution_rejection"]["properties"]["reason"] = enum(
    "invalid_contribution",
    "unauthorized_contribution",
    "contribution_scope",
    "already_contributed",
    "contribution_capacity",
    "id_conflict",
    "terminal",
    "host_authority_required",
    "unsupported_extension",
    "host_policy_required",
    "invalid_room_policy",
    "room_access",
    "agent_binding_required",
    "role_assignment",
    "room_permission",
    "invalid_time",
    "clock_regression",
)
D["contribution_decision"] = {
    "oneOf": [
        unchecked_marker(D["contribution_accepted"]),
        unchecked_marker(
            obj(
                {
                    **copy.deepcopy(D["contribution_accepted"]["properties"]),
                    "status": const("duplicate_contribution"),
                }
            )
        ),
        ref("contribution_rejection"),
    ]
}
D["contribution_receipts"] = mapping(
    obj({"fingerprint": ref("hash"), "decision": ref("contribution_accepted")}),
    ref("id"),
)
for suffix in ["", "_redacted"]:
    D["state" + suffix]["properties"]["contribution_receipts"] = ref(
        "contribution_receipts"
    )
    D["checkpoint_data" + suffix]["properties"]["profile"] = enum(
        "band-contract/state/2",
        "band-contract/state/3",
        "band-contract/state/4",
        "band-contract/state/5",
    )
    D["checkpoint_data" + suffix]["allOf"] = [
        {
            "if": {"properties": {"profile": const("band-contract/state/5")}},
            "then": {"properties": {"state": {"required": ["contribution_receipts"]}}},
            "else": {
                "properties": {
                    "state": {"not": {"required": ["contribution_receipts"]}}
                }
            },
        }
    ]
D["contribution_control"] = obj(
    {
        "operation": const("stop_contribution"),
        "session": ref("id"),
        "collection": ref("identifier"),
        "entry": ref("positive"),
        "member": ref("member_name"),
    }
)
D["view"]["properties"]["contribution_controls"] = array(ref("contribution_control"))
add_http_wire_schemas(D)

if __name__ == "__main__":
    path = (
        Path(__file__).resolve().parents[1]
        / "schemas/protocol-contributions.schema.json"
    )
    path.write_text(json.dumps(root, indent=2) + "\n")
    sys.stdout.write(str(f"{len(D)} definitions written") + "\n")
