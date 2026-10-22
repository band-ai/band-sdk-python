"""Portable H8 N1 oracle: result marking must not depend on admission success."""

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

PATH = ROOT / "spec/conformance/v2-unchecked-markers.json"
CONTENT = """---
name: unchecked-markers
description: Every local unchecked result identifies its policy mode.
engine: 0.2.0-lab.1
roles: [lead, worker]
extensions: [host.presence/1]
---
contract @infinity => expired
type notice
state new
  event lead -> worker: notice()
  on worker offline for 1m => expired
end expired
"""


def requests(unchecked: bool = True) -> list[dict[str, Any]]:
    source = {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": CONTENT,
        "sha256": hashlib.sha256(CONTENT.encode()).hexdigest(),
    }
    bindings = {"lead": "L", "worker": "W"}
    policy = {"mode": "unchecked"} if unchecked else trusted_policy(bindings)
    message = {"id": "m1", "session": "s", "kind": "notice", "payload": {}}
    event = {
        "id": "h1",
        "sequence": 1,
        "subject": "worker",
        "principal": "W",
        "kind": "presence",
        "value": "offline",
        "at": 0,
    }
    per_call = {} if unchecked else {"room_policy": policy}
    return [
        {"op": "compile", "handle": "c", "source": source},
        {
            "op": "new",
            "handle": "s",
            "contract": "c",
            "config": {
                "session": "s",
                "bindings": bindings,
                "now": 0,
                "max_messages": 10,
                "capabilities": ["host.presence/1"],
                "max_controls": 10,
                "room_policy": policy,
            },
        },
        {
            "op": "step",
            "handle": "s",
            "actor": "intruder",
            "now": 0,
            "message": message,
            **per_call,
        },
        {
            "op": "step",
            "handle": "s",
            "actor": "L",
            "now": -1,
            "message": message,
            **per_call,
        },
        {"op": "host_signal", "handle": "s", "event": event, "now": 0},
        {"op": "host_signal", "handle": "s", "event": copy.deepcopy(event), "now": 0},
        {"op": "host_signal", "handle": "s", "event": {}, "now": 0},
        {"op": "tick", "handle": "s", "now": 0},
        {"op": "tick", "handle": "s", "now": -1},
    ]


def verify(observed: list[dict[str, Any]], unchecked: bool = True) -> bool:
    assert len(observed) == 9, "H8 response count differs"
    assert "ok" in observed[0] and "ok" in observed[1], "H8 setup failed"
    expected = [
        ("rejected", "unauthorized"),
        ("rejected", "invalid_time"),
        ("host_accepted", None),
        ("duplicate_host_event", None),
        ("rejected", "invalid_host_event"),
        ("ticked", None),
        ("rejected", "invalid_time"),
    ]
    for index, (status, reason) in enumerate(expected, 2):
        decision = observed[index]["decision"]
        assert (decision["status"], decision.get("reason")) == (status, reason), (
            index,
            decision,
        )
        if unchecked:
            assert decision.get("policy") == "unchecked", (
                f"Unchecked result {index} lacks marker"
            )
        else:
            assert "policy" not in decision, f"Checked result {index} falsely marked"
    for index, previous in [(2, 1), (3, 2), (5, 4), (6, 5), (8, 7)]:
        old = observed[previous].get("state", observed[previous].get("ok"))
        assert observed[index]["state"] == old, f"Rejection/retry {index} mutated state"
    return True


def check(
    fixture: dict[str, Any], observed: list[dict[str, Any]], unchecked: bool = True
) -> None:
    lane = "unchecked" if unchecked else "checked"
    assert fixture["requests"][lane] == requests(unchecked), (
        "H8 requests or policy mode changed"
    )
    verify(fixture["expected"][lane], unchecked)
    verify(observed, unchecked)
    assert fixture["expected"][lane] == observed, "H8 portable responses differ"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    traces = {
        lane: adapter(requests(mode))
        for lane, mode in [("unchecked", True), ("checked", False)]
    }
    for lane, mode in [("unchecked", True), ("checked", False)]:
        verify(traces[lane], mode)
    fixture = {
        "profile": "band-contract/conformance-case/2",
        "id": "unchecked-markers",
        "requirements": ["V2-POLICY"],
        "requests": {
            lane: requests(mode)
            for lane, mode in [("unchecked", True), ("checked", False)]
        },
        "expected": traces,
    }
    if args.write:
        PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
    else:
        recorded = json.loads(PATH.read_text())
        for lane, mode in [("unchecked", True), ("checked", False)]:
            check(recorded, traces[lane], mode)
    sys.stdout.write(
        "H8 unchecked/checked portable result markers passed (18 requests)" + "\n"
    )


if __name__ == "__main__":
    main()
