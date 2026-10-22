"""Add reusable participant schemas while preserving the earlier schema bundles."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from contracts.spec.tools.buildcoordinationschemas import root as base
from contracts.spec.tools.buildv2schemas import add_http_wire_schemas
from contracts.spec.tools.schemacommon import const, enum, mapping, obj, ref

root = copy.deepcopy(base)
root["$id"] = "https://band.ai/spec/contract/protocol/4"
root["title"] = "Multi-agent skills: reusable participant types"
D = root["$defs"]
D["participant_field"] = copy.deepcopy(D["field_type"])
for variant in D["participant_field"]["oneOf"]:
    kind = variant["properties"]["kind"]
    if "enum" in kind and "ref" in kind["enum"]:
        kind["enum"].remove("ref")
D["participant_type"] = obj(
    {
        "profile": const("band-contract/participant/1"),
        "name": ref("identifier"),
        "fields": mapping(ref("participant_field"), ref("identifier")),
        "meta": ref("metadata"),
    }
)
D["participant_types"] = mapping(ref("participant_type"), ref("identifier"))
D["participant_attributes"] = {
    **mapping(
        {"oneOf": [ref("string"), ref("safe_integer"), {"type": "boolean"}]},
        ref("identifier"),
    ),
    "x-maxUtf8Bytes": 16384,
    "description": "The engine also checks this object against its declared participant type and limits canonical encoded UTF-8 bytes.",
}
D["roster_member"]["properties"]["attributes"] = ref("participant_attributes")
D["role_sets"] = mapping(
    {
        "oneOf": [
            obj({"profile": const("band-contract/roster/1"), "member": const("agent")}),
            obj(
                {
                    "profile": const("band-contract/roster/2"),
                    "member": ref("identifier"),
                }
            ),
        ]
    },
    ref("card_name"),
)
D["compiled"]["properties"]["participant_types"] = ref("participant_types")
D["role_definition"]["properties"]["contract"]["properties"]["participant_types"] = ref(
    "participant_types"
)
D["role_metadata"]["properties"].update(
    {"participant_type": ref("identifier"), "attributes": ref("participant_attributes")}
)
for suffix in ["", "_redacted"]:
    D["checkpoint_data" + suffix]["properties"]["profile"] = enum(
        "band-contract/state/2", "band-contract/state/3", "band-contract/state/4"
    )

add_http_wire_schemas(D)

if __name__ == "__main__":
    path = (
        Path(__file__).resolve().parents[1]
        / "schemas/protocol-participants.schema.json"
    )
    path.write_text(json.dumps(root, indent=2) + "\n")
    sys.stdout.write(str(f"{len(D)} definitions written") + "\n")
