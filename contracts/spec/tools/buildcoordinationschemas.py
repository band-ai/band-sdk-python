"""Add explicit coordination profiles without rewriting the pinned v1/v2 bundles."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from contracts.spec.tools.buildv2schemas import add_http_wire_schemas
from contracts.spec.tools.buildv2schemas import root as base
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
root["$id"] = "https://band.ai/spec/contract/protocol/3"
root["title"] = "Multi-agent skills: roster, collection and renewable-clock profiles"
D = root["$defs"]
# Runtime member names contain a stable collection/member separator. Authored
# identifiers still undergo the compiler's stricter grammar.
D["member_name"] = {
    "type": "string",
    "pattern": r"^[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_]{0,63})?$",
    "maxLength": 256,
}


def members(value: Any) -> Any:
    if isinstance(value, dict):
        if value == ref("card_name"):
            return ref("member_name")
        return {k: members(v) for k, v in value.items()}
    if isinstance(value, list):
        return [members(v) for v in value]
    return value


for key in [
    "rule",
    "duty",
    "obligation",
    "role_metadata",
    "fact",
    "accepted",
    "decision",
    "state",
    "state_redacted",
    "history_entry",
    "contract_extensions",
]:
    if key in D:
        D[key] = members(D[key])
D["roster_member"] = obj(
    {
        "name": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
        "actor": ref("id"),
        "guidance": {"type": "string", "x-maxUtf8Bytes": 16384},
    },
    ["guidance"],
)
D["roster_config"] = mapping(
    {
        "oneOf": [
            ref("id"),
            {**array(ref("roster_member")), "minItems": 1, "maxItems": 255},
        ]
    },
    ref("card_name"),
)
D["role_sets"] = mapping(
    obj({"profile": const("band-contract/roster/1"), "member": const("agent")}),
    ref("card_name"),
)
D["config"]["properties"]["bindings"] = ref("roster_config")
D["config"]["properties"]["initial_presence"] = mapping(
    obj({"value": enum("online", "offline", "unknown"), "since": ref("uint")}),
    ref("member_name"),
)
D["timeout_v2"] = obj(
    {
        "profile": const("band-contract/timeout/2"),
        "after_ms": nullable(ref("positive")),
        "maximum_ms": nullable(ref("positive")),
        "renewable": {"type": "boolean"},
        "outcome": ref("identifier"),
        "meta": ref("metadata"),
    }
)
D["timeout_any"] = {"oneOf": [ref("timeout"), ref("timeout_v2")]}
D["reset"] = {
    "oneOf": [
        obj(
            {
                "profile": const("band-contract/renewal/1"),
                "scope": enum("state", "contract"),
            }
        ),
        obj(
            {
                "profile": const("band-contract/renewal/1"),
                "scope": const("rule"),
                "rule": ref("id"),
                "duty": ref("id"),
            }
        ),
        obj(
            {
                "profile": const("band-contract/renewal/1"),
                "scope": const("collection"),
                "rule": ref("id"),
                "collection": ref("identifier"),
            }
        ),
    ]
}
D["rule"]["properties"].update(
    {
        "reset": ref("reset"),
        "collection": ref("identifier"),
        "settlement": enum("ok", "failed"),
    }
)
D["collection_definition"] = obj(
    {
        "profile": const("band-contract/collection/1"),
        "name": ref("identifier"),
        "state": ref("identifier"),
        "from": array(ref("member_name"), unique=True),
        "to": array(ref("member_name"), unique=True),
        "reply": ref("identifier"),
        "after_ms": ref("positive"),
        "offline_ms": nullable(ref("positive")),
        "target": ref("identifier"),
        "meta": ref("metadata"),
    }
)
# DF-25: an optional collection quorum; null attribute means every member (`quorum all`).
D["collection_definition"]["properties"]["quorum"] = obj(
    {"attribute": nullable(ref("identifier")), "target": ref("identifier")}
)
# DF-21: an optional cumulative state budget, its expiry reason and the per-state dwell it needs.
D["total"] = obj(
    {
        "profile": const("band-contract/total/1"),
        "after_ms": ref("positive"),
        "outcome": ref("identifier"),
        "meta": ref("metadata"),
    }
)
D["state_declaration"]["properties"]["total"] = ref("total")
D["expiry"]["oneOf"][0]["properties"]["reason"]["enum"].append("state_total")
D["history_entry"]["oneOf"][1]["properties"]["reason"]["enum"].append("state_total")
for key in ["compiled"]:
    D[key]["properties"].update(
        {
            "timeout": ref("timeout_any"),
            "role_sets": ref("role_sets"),
            "collections": mapping(ref("collection_definition"), ref("identifier")),
            "renewal_profile": const("band-contract/renewal/1"),
        }
    )
D["role_definition"]["properties"]["contract"]["properties"].update(
    {
        k: D["compiled"]["properties"][k]
        for k in ["timeout", "role_sets", "collections", "renewal_profile"]
    }
)
D["role_metadata"]["properties"].update(
    {"collection": ref("card_name"), "assignment": ref("string")}
)
D["collection_member"] = obj(
    {
        "status": enum("pending", "ok", "failed", "offline", "timed_out", "cancelled"),
        "message": nullable(ref("id")),
        "at": nullable(ref("uint")),
    }
)
D["collection_record"] = obj(
    {
        "name": ref("identifier"),
        "state": ref("identifier"),
        "entry": ref("positive"),
        "started_at": ref("uint"),
        "deadline_at": ref("uint"),
        "closed_at": nullable(ref("uint")),
        "reason": nullable(enum("settled", "deadline", "cancelled")),
        "from": array(ref("member_name"), unique=True),
        "to": array(ref("member_name"), unique=True),
        "members": mapping(ref("collection_member"), ref("member_name")),
    }
)
extra = {
    "clocks": obj(
        {"phase_at": nullable(ref("uint")), "hard_at": nullable(ref("uint"))}
    ),
    "dwell": mapping(ref("uint"), ref("identifier")),
    "roster_config": ref("roster_config"),
    "collections": mapping(ref("collection_record"), ref("identifier")),
    "collection_results": array(ref("collection_record")),
    "collection_log": array(mapping(ref("json"))),
}
for suffix in ["", "_redacted"]:
    D["state" + suffix]["properties"].update(extra)
    D["checkpoint_data" + suffix]["properties"]["profile"] = enum(
        "band-contract/state/2", "band-contract/state/3"
    )
D["view"]["properties"].update(
    {k: v for k, v in extra.items() if k in ["collections", "collection_results"]}
)


# Receipts and projections carry concrete member names, never arbitrary role
# declarations. This replacement is restricted to semantic role fields.
def runtime_roles(value: Any) -> Any:
    if isinstance(value, dict):
        out = {k: runtime_roles(v) for k, v in value.items()}
        if (
            "properties" in out
            and "role" in out["properties"]
            and out["properties"]["role"] == ref("card_name")
        ):
            out["properties"]["role"] = ref("member_name")
        return out
    if isinstance(value, list):
        return [runtime_roles(v) for v in value]
    return value


for key in list(D):
    D[key] = runtime_roles(D[key])
add_http_wire_schemas(D)

if __name__ == "__main__":
    p = (
        Path(__file__).resolve().parents[1]
        / "schemas/protocol-coordination.schema.json"
    )
    p.write_text(json.dumps(root, indent=2) + "\n")
    sys.stdout.write(str(f"{len(D)} definitions written") + "\n")
