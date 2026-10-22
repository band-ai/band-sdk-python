"""M21 independent fail-closed oracles, with exact packet hashes recomputed after mutations."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.vectorinputs import file_source
from remote_agent.protocol import digest


def cases() -> list[dict[str, Any]]:
    rows = []
    bundle = ROOT / "spec/conformance"
    restore = json.loads((bundle / "case-v2-checkpoint-restore.json").read_text())
    local = json.loads((bundle / "case-v2-portable-local-preflight.json").read_text())
    compile = copy.deepcopy(restore["requests"][0])
    packet = copy.deepcopy(restore["requests"][1]["checkpoint"])

    def assertion(i: int, path: str, value: Any) -> dict[str, Any]:
        return {"response": i, "pointer": path, "equals": value}

    def add(
        name: str,
        requests: list[dict[str, Any]],
        assertions: list[dict[str, Any]],
        schema_assertions: Sequence[dict[str, Any]] = (),
    ) -> None:
        # Every row compiles assignment, which waits for replies: the replies schema registry.
        row = {
            "profile": "band-contract/conformance-case/1",
            "id": "v2-m21-" + name,
            "requirements": ["DSL-003", "ENG-015", "ENG-016"],
            "schema_profile": 6,
            "requests": requests,
            "assertions": assertions,
        }
        if schema_assertions:
            row["source_schema_assertions"] = list(schema_assertions)
        rows.append(row)

    def restore_request(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "op": "restore",
            "contract": "c",
            "handle": "s",
            "checkpoint": value,
            "now": 0,
        }

    def rehash(value: dict[str, Any]) -> dict[str, Any]:
        value["hash"] = digest(value["data"])
        return value

    raw = compile["source"]["content"]
    unknown = copy.deepcopy(compile)
    unknown["source"] = file_source(
        raw.replace("engine: 0.2.0-lab.1", "engine: 0.99.0-lab.1", 1)
    )
    # The spec text wins (spec/README.md; Ada's set N decision B1): an unknown engine is compiler error engine at line 1.
    add(
        "unknown-engine",
        [unknown],
        [assertion(0, "/error/code", "engine"), assertion(0, "/error/line", 1)],
    )
    wrong_hash = copy.deepcopy(compile)
    wrong_hash["source"]["sha256"] = "0" * 64
    extra = copy.deepcopy(compile)
    extra["source"]["untrusted"] = "extra"
    bad_format = copy.deepcopy(compile)
    bad_format["source"]["format"] = "unknown"
    add(
        "file-integrity",
        [wrong_hash, extra, bad_format],
        [
            assertion(0, "/error", "file_digest_mismatch"),
            assertion(1, "/error", "input"),
            assertion(2, "/error", "input"),
        ],
        [{"request": i, "valid": False} for i in (1, 2)],
    )
    changed = copy.deepcopy(compile)
    changed["source"] = file_source(raw + "\n%% Changed file identity.\n")
    add(
        "source-pin",
        [changed, restore_request(packet)],
        [
            assertion(0, "/ok/digest", packet["data"]["definition"]),
            assertion(0, "/ok/source_digest", changed["source"]["sha256"]),
            assertion(1, "/error", "contract_mismatch"),
        ],
    )
    state = copy.deepcopy(packet)
    state["data"]["profile"] = "band-contract/state/999"
    add(
        "state-profile",
        [compile, restore_request(rehash(state))],
        [assertion(1, "/error", "invalid_checkpoint")],
    )
    checkpoint = copy.deepcopy(packet)
    checkpoint["profile"] = "band-contract/room-checkpoint/999"
    add(
        "checkpoint-profile",
        [compile, restore_request(checkpoint)],
        [assertion(1, "/error", "invalid_checkpoint")],
    )
    check = copy.deepcopy(local["requests"][1])
    check["projection"]["profile"] = "band-contract/agent-view/999"
    add(
        "view-profile",
        [compile, check],
        [
            assertion(1, "/status", "rejected"),
            assertion(1, "/reason", "invalid_checkpoint"),
        ],
    )
    cross = copy.deepcopy(packet)
    cross["data"]["state"]["roster_config"] = {
        "lead": "lead",
        "reviewers": [
            {
                "actor": "member",
                "name": "member",
                "attributes": {"specialty": "backend"},
            }
        ],
    }
    # Still labeled with the base profile (state/6: assignment waits for replies), but carries
    # state/4-only payload fields, with a valid checksum.
    assert cross["data"]["profile"] == "band-contract/state/6"
    add(
        "cross-profile-state",
        [compile, restore_request(rehash(cross))],
        [assertion(1, "/error", "invalid_checkpoint")],
    )
    return rows
