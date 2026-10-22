"""Check independent inbox vectors; optionally compare a trusted JSON-lines adapter."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema

from contracts.spec.tools.adapterdriver import run as run_adapter
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from remote_agent.protocol import SAFE_INTEGER, canonical, decode

PATH = ROOT / "spec/conformance/inbox-vectors.json"

PAGE_BYTES = 2_097_152
# The one inbox error code shared by every contract-room/inbox/1 binding.
CURSOR_AHEAD = "cursor_ahead"


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validators() -> dict[str, jsonschema.Draft202012Validator]:
    schema = json.loads((ROOT / "spec/schemas/protocol-v2.schema.json").read_text())
    return {
        name: jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/" + name})
        for name in ("accepted", "inbox_request", "inbox_response")
    }


def receipts(
    bundle: dict[str, Any], validate: dict[str, jsonschema.Draft202012Validator]
) -> dict[str, dict[str, Any]]:
    expanded = {}
    for name, template in bundle["receipts"].items():
        require(
            set(template) == {"decision", "text_fill", "canonical_bytes"},
            "receipt template fields",
        )
        fill = template["text_fill"]
        require(
            set(fill) == {"scalar", "count"}
            and isinstance(fill["scalar"], str)
            and len(fill["scalar"]) == 1
            and type(fill["count"]) is int
            and 0 <= fill["count"] <= PAGE_BYTES + 1,
            "bounded scalar expansion",
        )
        decision = copy.deepcopy(template["decision"])
        require(
            decision.get("payload", {}).get("text") == "",
            "template text must start empty",
        )
        decision["payload"]["text"] = fill["scalar"] * fill["count"]
        require(
            validate["accepted"].is_valid(decision), "original accepted decision schema"
        )
        require(decision["id"] == name, "receipt ID must match its template name")
        require(
            type(template["canonical_bytes"]) is int
            and len(canonical(decision)) == template["canonical_bytes"],
            "canonical UTF-8 byte pin differs",
        )
        expanded[name] = decision
    return expanded


def expected_response(case: dict[str, Any], expanded: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    require(
        type(expected.get("status")) is int
        and expected["status"] in (200, 400, 409, 503),
        "response status",
    )
    if expected["status"] == 409:
        require(
            set(expected) == {"status", "body"}
            and expected["body"] == {"code": CURSOR_AHEAD},
            "cursor conflict requires cursor_ahead without a cursor or events",
        )
        return expected
    if expected["status"] != 200:
        require(set(expected) == {"status"}, "failed page cannot acknowledge a cursor")
        return expected
    require(
        set(expected) == {"status", "body"}
        and set(expected["body"]) == {"cursor", "events"},
        "success response fields",
    )
    return {
        "status": 200,
        "body": {
            "cursor": expected["body"]["cursor"],
            "events": [expanded[name] for name in expected["body"]["events"]],
        },
    }


def page(
    case: dict[str, Any],
    expanded: dict[str, Any],
    validate: dict[str, jsonschema.Draft202012Validator],
) -> dict[str, Any]:
    """Executable specification oracle, independent of any room/storage implementation."""
    request = case["request"]
    if not validate["inbox_request"].is_valid(request):
        return {"status": 400}
    if request["after"] > case["head"]:
        return {"status": 409, "body": {"code": CURSOR_AHEAD}}
    after = request["after"]
    selected = [row for row in case["records"] if row["position"] > after][
        : request["limit"]
    ]
    cursor, events, size = after, [], 0
    for row in selected:
        decision = expanded[row["receipt"]] if row["receipt"] is not None else None
        if decision is not None and case["actor"] in decision["recipients"]:
            event_bytes = len(canonical(decision))
            if size + event_bytes > PAGE_BYTES:
                if not events:
                    return {"status": 503}
                break
            events.append(decision)
            size += event_bytes
        cursor = row["position"]
    return {"status": 200, "body": {"cursor": cursor, "events": events}}


def check(
    bundle: dict[str, Any], observed: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    require(
        bundle["profile"] == "band-contract/inbox-vectors/1"
        and bundle["wire_profile"] == "contract-room/inbox/1"
        and bundle["qualification"] == "not_assessed"
        and bundle["event_bytes_limit"] == PAGE_BYTES,
        "inbox vector identity and bounds",
    )
    validate = validators()
    expanded = receipts(bundle, validate)
    cases = bundle["cases"]
    require(
        cases and len({case["id"] for case in cases}) == len(cases),
        "unique nonempty vector inventory",
    )
    by_id = {case["id"]: case for case in cases}
    if observed is not None:
        require(len(observed) == len(cases), "adapter response count differs")
    for index, case in enumerate(cases):
        require(
            case["storage"] in ("live", "restart", "compacted", "archived"),
            "storage scenario",
        )
        if "replay_of" in case:
            require(
                case["storage"] != "live" and case["replay_of"] in by_id,
                "retention baseline",
            )
            baseline = by_id[case["replay_of"]]
            require(
                baseline["storage"] == "live"
                and all(
                    case[key] == baseline[key]
                    for key in (
                        "session",
                        "actor",
                        "head",
                        "records",
                        "request",
                        "expected",
                    )
                ),
                "retention must preserve positions, decisions and paging: "
                + case["id"],
            )
        require(
            type(case["head"]) is int and 0 <= case["head"] <= SAFE_INTEGER,
            "durable head",
        )
        previous, identifiers = 0, set()
        for row in case["records"]:
            require(
                set(row) == {"position", "receipt"}
                and type(row["position"]) is int
                and previous < row["position"] <= case["head"],
                "stable ordered scan positions",
            )
            previous = row["position"]
            if row["receipt"] is not None:
                decision = expanded[row["receipt"]]
                require(
                    decision["session"] == case["session"],
                    "receipt belongs to another session",
                )
                require(
                    decision["id"] not in identifiers,
                    "duplicate acceptance occupies two positions",
                )
                identifiers.add(decision["id"])
        wanted = expected_response(case, expanded)
        require(
            page(case, expanded, validate) == wanted,
            "independent inbox expectation differs: " + case["id"],
        )
        if wanted["status"] == 200:
            require(
                validate["inbox_response"].is_valid(wanted["body"]),
                "portable response schema",
            )
        if observed is not None:
            actual = observed[index]
            # Error bodies are binding-specific, except the shared ahead-of-head code;
            # no failed page may acknowledge a cursor or events.
            if wanted["status"] != 200:
                require(
                    isinstance(actual, dict)
                    and actual.get("status") == wanted["status"]
                    and "cursor" not in actual
                    and "events" not in actual
                    and not (
                        isinstance(actual.get("body"), dict)
                        and ({"cursor", "events"} & actual["body"].keys())
                    ),
                    "adapter failed page acknowledges input: " + case["id"],
                )
                if wanted["status"] == 409:
                    body = actual.get("body")
                    require(
                        isinstance(body, dict)
                        and body.get("code") == CURSOR_AHEAD
                        and isinstance(body.get("error"), str),
                        'adapter 409 lacks {error, code:"cursor_ahead"}: ' + case["id"],
                    )
            else:
                require(
                    actual == wanted, "adapter inbox response differs: " + case["id"]
                )
    return {
        "profile": "band-contract/inbox-vector-check/1",
        "cases": len(cases),
        "vector_oracle_checked": True,
        "adapter_checked": observed is not None,
        "band_live_checked": False,
        "qualification": "not_assessed",
        "passed": True,
    }


def adapter_requests(bundle: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Compact trusted harness inputs; expected results never enter the adapter."""
    for case in bundle["cases"]:
        names = {
            row["receipt"] for row in case["records"] if row["receipt"] is not None
        }
        yield {
            **{key: value for key, value in case.items() if key != "expected"},
            "profile": bundle["profile"],
            "event_bytes_limit": PAGE_BYTES,
            "receipts": {name: bundle["receipts"][name] for name in sorted(names)},
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=PATH)
    parser.add_argument(
        "--adapter-config",
        type=Path,
        help="JSON array of trusted adapter command arguments",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bundle = json.loads(args.fixture.read_text())
    report = check(bundle)
    if args.adapter_config:
        # The harness must preserve intentionally invalid/unsafe request numbers for 400 cases.
        wire = "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in adapter_requests(bundle)
        ).encode("utf-8")
        raw = run_adapter(json.loads(args.adapter_config.read_text()), wire, cwd=ROOT)
        report = check(
            bundle, [decode(line.decode("utf-8")) for line in raw.splitlines()]
        )
    report["fixture_sha256"] = hashlib.sha256(args.fixture.read_bytes()).hexdigest()
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(str(json.dumps(report)) + "\n")


if __name__ == "__main__":
    main()
