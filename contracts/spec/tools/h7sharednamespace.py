"""Portable H7 shared-ID trace with an independent, actor-sensitive oracle."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from typing import Any

from contracts.spec.tools.generatevectors import adapter
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.vectorinputs import trusted_policy

PATH = ROOT / "spec/conformance/v2-h7-shared-namespace.json"
CONTENT = """---
name: h7-shared-namespace
description: A message ID belongs to one actor in the session-wide namespace.
engine: 0.2.0-lab.1
roles: [a, b]
---
contract @infinity => expired
type notice
state new
  event b -> a: notice()
  event a -> b: notice()
end expired
"""


def requests() -> list[dict[str, Any]]:
    source = {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": CONTENT,
        "sha256": hashlib.sha256(CONTENT.encode()).hexdigest(),
    }
    claim = {"id": "shared:X", "session": "s", "kind": "notice", "payload": {}}
    bindings = {"a": "A", "b": "B"}
    # Explicit host-owned evidence keeps this namespace proof on the checked
    # profile after H8. It is not a policy chosen by an agent proposal.
    policy = trusted_policy(bindings)

    def step(actor: str, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "op": "step",
            "handle": "s",
            "actor": actor,
            "now": 0,
            "room_policy": policy,
            "message": message,
        }

    return [
        {"op": "compile", "handle": "c", "source": source},
        {
            "op": "new",
            "handle": "s",
            "contract": "c",
            "config": {
                "session": "s",
                "bindings": bindings,
                "room_policy": policy,
                "now": 0,
                "max_messages": 10,
            },
        },
        step("B", copy.deepcopy(claim)),
        step("A", copy.deepcopy(claim)),
        step("B", copy.deepcopy(claim)),
        step("A", {**claim, "id": "independent:Y"}),
    ]


def verify(observed: list[dict[str, Any]]) -> bool:
    """Do not derive these obligations from a fixture's editable assertions."""
    assert len(observed) == 6, "H7 trace response count differs"
    first, reuse, retry, fresh = (item["decision"] for item in observed[2:])
    assert (first["status"], first["actor"], first["role"], first["id"]) == (
        "accepted",
        "B",
        "b",
        "shared:X",
    ), "B did not claim X"
    assert (reuse["status"], reuse.get("reason")) == ("rejected", "id_conflict"), (
        "Cross-actor ID reuse must be id_conflict"
    )
    assert observed[3]["state"] == observed[2]["state"], (
        "Rejected A changed authoritative state"
    )
    assert retry["status"] == "duplicate", "B's exact retry was not retained"
    assert retry["original"] == first, "Duplicate receipt lost B's ownership"
    assert (fresh["status"], fresh["actor"], fresh["role"]) == ("accepted", "A", "a"), (
        "A was not authorized with an independent ID"
    )
    return True


def check(fixture: dict[str, Any], observed: list[dict[str, Any]]) -> None:
    assert fixture["profile"] == "band-contract/conformance-case/2"
    assert fixture["id"] == "h7-shared-namespace"
    assert fixture["requests"] == requests(), (
        "H7 attack requests or actor binding changed"
    )
    verify(fixture["expected"])
    verify(observed)
    assert observed == fixture["expected"], "H7 portable expected responses differ"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    observed = adapter(requests())
    verify(observed)
    fixture = {
        "profile": "band-contract/conformance-case/2",
        "id": "h7-shared-namespace",
        "requirements": ["ENG-007", "ENG-012"],
        "requests": requests(),
        "expected": observed,
        "assertions": [
            {"response": 2, "pointer": "/decision/status", "equals": "accepted"},
            {"response": 3, "pointer": "/decision/reason", "equals": "id_conflict"},
            {"response": 4, "pointer": "/decision/status", "equals": "duplicate"},
            {"response": 5, "pointer": "/decision/status", "equals": "accepted"},
        ],
    }
    if args.write:
        PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    else:
        check(json.loads(PATH.read_text()), observed)
    sys.stdout.write(
        str(
            json.dumps(
                {"case": fixture["id"], "requests": len(observed), "passed": True}
            )
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
