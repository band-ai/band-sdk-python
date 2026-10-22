"""Reply alternatives (contract.replies/1, DESIGN v2 s5): protocol/6 extends protocol/5.

The compiled rule gains `literals` and `wait`; a wait state W gains `wait`; the renewal reset
gains the `wait` scope; the saved state gains `wait` under band-contract/state/6; views show
the open wait with its alternatives and every exit from W with its sender roles. Earlier bundles stay
immutable, so a protocol/5 reader refuses all of it (S6).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from contracts.spec.tools.buildcontributionschemas import root as base
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
root["$id"] = "https://band.ai/spec/contract/protocol/6"
root["title"] = "Multi-agent skills: reply alternatives"
D = root["$defs"]
D["card"]["properties"]["extensions"]["items"]["enum"].append("contract.replies/1")

D["duty_id"] = {"type": "string", "pattern": "^due-[0-9]+$"}
D["rule_id"] = {"type": "string", "pattern": "^rule-[0-9]+$"}
# A literal names an enum member or is a JSON boolean; text, integer and ref take none.
D["reply_literals"] = {
    **mapping({"anyOf": [ref("identifier"), {"type": "boolean"}]}, ref("identifier")),
    "maxProperties": 4,
}
D["rule"]["properties"]["literals"] = ref("reply_literals")
D["rule"]["properties"]["wait"] = ref("duty_id")
D["reset"]["oneOf"].append(
    obj(
        {
            "profile": const("band-contract/renewal/1"),
            "scope": const("wait"),
            "duty": ref("duty_id"),
        }
    )
)

# Every state-moving rule that applies in W, other than its alternatives, and who sends it (K-2).
D["wait_exit"] = obj(
    {
        "rule": ref("rule_id"),
        "kind": ref("identifier"),
        "target": ref("identifier"),
        "from": array(ref("member_name"), 1, True),
    }
)
D["wait_definition"] = obj(
    {
        "id": ref("duty_id"),
        "heads": array(ref("rule_id"), 1, True),
        "from": {**array(ref("member_name"), 1, True), "maxItems": 1},
        "to": {**array(ref("member_name"), 1, True), "maxItems": 1},
        "kinds": array(ref("identifier"), 1, True),
        "after_ms": ref("positive"),
        "outcome": ref("identifier"),
        "alternatives": {**array(ref("rule_id"), 1, True), "maxItems": 16},
        "meta": ref("metadata"),
        # Role definition views only: every exit from W with its sender roles (S1, K-2).
        "exits": array(ref("wait_exit")),
    },
    optional=("exits",),
)
D["state_declaration"]["properties"]["wait"] = ref("wait_definition")

D["saved_wait"] = obj(
    {
        "id": ref("duty_id"),
        "head": ref("id"),
        "due_at": ref("uint"),
        "remaining": array(ref("member_name"), 0, True),
    }
)
for suffix in ["", "_redacted"]:
    D["state" + suffix]["properties"]["wait"] = nullable(ref("saved_wait"))
    checkpoint = D["checkpoint_data" + suffix]
    checkpoint["properties"]["profile"]["enum"].append("band-contract/state/6")
    # Contribution receipts stay tied to a contributions contract, whichever profile names it.
    checkpoint["allOf"] = [
        {
            "if": {"properties": {"profile": const("band-contract/state/5")}},
            "then": {"properties": {"state": {"required": ["contribution_receipts"]}}},
        },
        {
            "if": {
                "properties": {
                    "profile": {
                        "not": enum("band-contract/state/5", "band-contract/state/6")
                    }
                }
            },
            "then": {
                "properties": {
                    "state": {"not": {"required": ["contribution_receipts"]}}
                }
            },
        },
        {
            "if": {"properties": {"profile": const("band-contract/state/6")}},
            "then": {"properties": {"state": {"required": ["wait"]}}},
            "else": {"properties": {"state": {"not": {"required": ["wait"]}}}},
        },
    ]

D["wait_alternative"] = obj(
    {
        "rule": ref("rule_id"),
        "kind": ref("identifier"),
        "literals": ref("reply_literals"),
        "target": ref("identifier"),
    }
)
D["view"]["properties"]["wait"] = nullable(
    obj(
        {
            **copy.deepcopy(D["saved_wait"]["properties"]),
            "alternatives": array(ref("wait_alternative"), 1),
            "exits": array(ref("wait_exit")),
        }
    )
)

# After the extensions, so the compact HTTP view keeps `wait` like every other view field.
add_http_wire_schemas(D)

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "schemas/protocol-replies.schema.json"
    path.write_text(json.dumps(root, indent=2) + "\n")
    sys.stdout.write(str(f"{len(D)} definitions written") + "\n")
