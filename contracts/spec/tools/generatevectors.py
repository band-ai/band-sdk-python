"""Explicitly regenerate reference vectors, checking independent assertions before writing."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from contracts.spec.tools.adapterdriver import run as run_adapter
from contracts.spec.tools.bandexamples import wire_examples as band_wire_examples
from contracts.spec.tools.buildcoverage import coverage
from contracts.spec.tools.carriervectors import cases as carrier_cases
from contracts.spec.tools.negativevectorsm21 import cases as fail_closed_cases
from contracts.spec.tools.negativevectorsm22 import cases as safety_cases
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.vectorinputs import hosted, source
from contracts.spec.tools.vectorsv2extras import cases as extra_cases
from remote_agent.protocol import canonical, decode

# D-15: a missing room_policy fails closed. The harness is the trusted host: every bound
# actor is an active agent owner (so any band.permissions level passes) at a fixed revision.
# Checked sessions export no policy key, so stored states are unchanged by the evidence.


def cases(prefix: str | None = None) -> list[dict[str, Any]]:
    # Neither compatibility fixtures nor legacy corpus files are generation inputs.
    if prefix and prefix.startswith("carrier-"):
        return [
            {**case, "requests": hosted(case["requests"])}
            for case in carrier_cases()
            if case["id"].startswith(prefix)
        ]
    data = json.loads((ROOT / "spec/conformance/semantic-inputs-v2.json").read_text())
    result = [
        case for case in data["cases"] if not prefix or case["id"].startswith(prefix)
    ]
    needs_checkpoint = any(
        case["id"]
        in (
            "v2-checkpoint-restore",
            "v2-checkpoint-corruption",
            "v2-portable-local-preflight",
        )
        for case in result
    )
    if needs_checkpoint:
        requests = [
            {"op": "compile", "handle": "c", "source": source("assignment")},
            {
                "op": "new",
                "contract": "c",
                "handle": "s",
                "config": {
                    "session": "s",
                    "bindings": {
                        "lead": "lead",
                        "worker": "worker",
                        "reviewer": "reviewer",
                    },
                    "now": 0,
                    "inactivity": "accepted",
                    "max_messages": 100,
                    "max_bytes": 16384,
                    "capabilities": ["contract.replies/1"],
                },
            },
            {
                "op": "step",
                "handle": "s",
                "actor": "lead",
                "now": 0,
                "message": {
                    "id": "offer",
                    "session": "s",
                    "kind": "offer",
                    "payload": {"task": "Portable task"},
                },
            },
        ]
        observed = adapter(
            hosted(
                requests
                + [
                    {"op": "checkpoint", "handle": "s"},
                    {"op": "view", "handle": "s", "actor": "worker", "now": 0},
                ]
            )
        )
        packet, projection = observed[-2]["ok"], observed[-1]["ok"]

        def resolve(value: Any) -> Any:
            if isinstance(value, dict) and "$checkpoint" in value:
                checkpoint = copy.deepcopy(packet)
                if value["$checkpoint"] == "corrupt":
                    checkpoint["hash"] = "0" * 64
                return checkpoint
            if isinstance(value, dict) and "$projection" in value:
                return copy.deepcopy(projection)
            if isinstance(value, dict):
                return {k: resolve(v) for k, v in value.items()}
            if isinstance(value, list):
                return [resolve(v) for v in value]
            return value

        result = resolve(result)
    result.extend(
        case for case in extra_cases() if not prefix or case["id"].startswith(prefix)
    )
    result.extend(
        case
        for case in fail_closed_cases()
        if not prefix or case["id"].startswith(prefix)
    )
    result.extend(
        case for case in safety_cases() if not prefix or case["id"].startswith(prefix)
    )
    if not prefix or prefix.startswith("carrier-"):
        result.extend(carrier_cases())
    return [{**case, "requests": hosted(case["requests"])} for case in result]


# These are immutable review inputs, not generator outputs. Further supersessions
# require a separately approved metadata change and an explicit new digest here.
BASELINE_V1_SHA256 = "f2f62ddc8ae231d23da58354f32ccb48ceeb6452d2a109232f95117131e27cb0"
PIN_SUPERSESSIONS_SHA256 = (
    "c32d0d7731fa28e6cb78a35ddca87788e5b695a02f3fa9ebfc235e53932ec3cb"
)


def compatibility_pins(root: Path | None = None) -> dict[str, Any]:
    """Apply only Rowan's exact X1 L7 pins, without rewriting the v1 baseline.

    The approval file records old/new file AND manifest pins and their authority.
    Its own digest is fixed so editing it cannot silently authorize fixture drift.
    """
    bundle = (root or ROOT) / "spec/conformance"
    baseline_bytes = (bundle / "baseline-v1-pins.json").read_bytes()
    assert hashlib.sha256(baseline_bytes).hexdigest() == BASELINE_V1_SHA256, (
        "Original baseline digest changed"
    )
    approval_bytes = (bundle / "baseline-v1-pin-supersessions.json").read_bytes()
    assert hashlib.sha256(approval_bytes).hexdigest() == PIN_SUPERSESSIONS_SHA256, (
        "Pin supersession approval digest changed"
    )
    pins = json.loads(baseline_bytes)
    approval = json.loads(approval_bytes)
    assert approval["baseline_sha256"] == BASELINE_V1_SHA256, (
        "Supersession baseline mismatch"
    )
    assert set(approval["files"]) == {"openai-request.json", "openai-result.json"}, (
        "Unapproved supersession scope"
    )
    for name, change in approval["files"].items():
        assert pins["files"][name] == change["old_sha256"], (
            "Supersession old pin mismatch: " + name
        )
        pins["files"][name] = change["new_sha256"]
    for change in approval["manifest"]:
        kind, old, new = change["kind"], change["old"], change["new"]
        assert kind == "artifacts" and old["path"] in approval["files"], (
            "Unapproved manifest supersession"
        )
        assert new == {**old, "sha256": approval["files"][old["path"]]["new_sha256"]}, (
            "Nonhash manifest supersession"
        )
        assert old in pins["manifest"][kind], (
            "Supersession old manifest mismatch: " + old["path"]
        )
        pins["manifest"][kind][pins["manifest"][kind].index(old)] = new
    return pins


def check_parity(root: Path | None = None) -> dict[str, int]:
    root = root or ROOT
    bundle = root / "spec/conformance"
    baseline = compatibility_pins(root)
    mapping = json.loads((bundle / "assertion-parity-v2.json").read_text())
    expected = {
        f"{case['id']}:A{index:03d}": assertion
        for case in baseline["cases"]
        if case["legacy_source"]
        for index, assertion in enumerate(case["assertions"])
    }
    assert set(mapping) == set(expected), "Incomplete baseline assertion mapping"
    used = set()
    for key, entry in mapping.items():
        assert entry["baseline_assertion"] == expected[key], (
            "Baseline mapping drift: " + key
        )
        target = (entry["target_case"], entry["target_index"])
        assert target not in used, "Reused target assertion: " + key
        used.add(target)
        case = json.loads((bundle / ("case-" + target[0] + ".json")).read_text())
        assert target[0].startswith("v2-"), "Replacement is not a distinct current case"
        assert case["assertions"][target[1]] == entry["assertion"], (
            "Parity target drift: " + key
        )
        assert entry["authority"] and entry["reason"], "Unjustified remap: " + key
        for request in case["requests"]:
            if "source" in request:
                assert request["source"]["profile"] == "band-contract/file-source/1"
                assert (
                    hashlib.sha256(request["source"]["content"].encode()).hexdigest()
                    == request["source"]["sha256"]
                )
        assertion = entry["assertion"]
        assert (
            pointer(case["expected"][assertion["response"]], assertion["pointer"])
            == assertion["equals"]
        ), "Parity assertion fails: " + key
    manifest = json.loads((bundle / "manifest.json").read_text())
    removal = json.loads((bundle / "legacy-removal-v2.json").read_text())
    assert (
        hashlib.sha256((bundle / "baseline-v1-pins.json").read_bytes()).hexdigest()
        == removal["baseline_pins_sha256"]
    ), "Baseline pins changed"
    assert (
        hashlib.sha256((bundle / "assertion-parity-v2.json").read_bytes()).hexdigest()
        == removal["parity_map_sha256"]
        == "0106c2421c74aa5419499e6cb528164786ad31a788ed575160b4a137ef78d8c9"
    ), "Approved parity map changed"
    retired_ids = {case["id"] for case in baseline["cases"] if case["legacy_source"]}
    retired = [row for row in baseline["manifest"]["cases"] if row["id"] in retired_ids]
    assert removal["retired_cases"] == retired and len(retired) == 62, (
        "Incorrect retirement inventory"
    )
    retired_paths = {row["path"] for row in retired}
    assert all(not (bundle / name).exists() for name in retired_paths), (
        "Retired fixture still present"
    )
    assert all(row["id"] not in retired_ids for row in manifest["cases"]), (
        "Retired fixture in live manifest"
    )
    for name, sha256 in baseline["files"].items():
        if name not in retired_paths:
            assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == sha256, (
                "Compatibility fixture changed: " + name
            )
    for kind in ("cases", "artifacts"):
        for entry in baseline["manifest"][kind]:
            if kind == "cases" and entry["id"] in retired_ids:
                continue
            matching = [
                item for item in manifest[kind] if item["path"] == entry["path"]
            ]
            assert matching == [entry], (
                "Compatibility manifest entry changed: " + entry["path"]
            )
    # The retirement inventory records the original v1 pins; only X1's two approved provider pins are superseded.
    original = json.loads((bundle / "baseline-v1-pins.json").read_text())
    assert removal["retained_baseline_cases"] == [
        row for row in original["manifest"]["cases"] if row["id"] not in retired_ids
    ]
    assert removal["retained_baseline_artifacts"] == original["manifest"]["artifacts"]
    return {
        "legacy_cases": 62,
        "legacy_assertions": len(mapping),
        "unique_targets": len(used),
    }


def pointer(value: Any, path: str) -> Any:
    for part in path.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def reference_argv() -> list[str]:
    paths = sorted((ROOT / "contract-lab/_build/dev/lib").glob("*/ebin"))
    return (
        ["elixir", "--erl", "+S 4:4"]
        + [item for path in paths for item in ("-pa", str(path))]
        + [str(ROOT / "spec/tools/reference_adapter.exs")]
    )


def checked_argv(argv: Any) -> list[str]:
    """A supplied adapter configuration: a non-empty list of non-empty strings, never a falsy stand-in."""
    if not (
        isinstance(argv, list)
        and argv
        and all(isinstance(item, str) and item for item in argv)
    ):
        raise ValueError(
            "An adapter configuration must be a non-empty JSON array of non-empty strings"
        )
    return argv


def adapter_argv(argv: list[str] | None) -> list[str]:
    """The argv adapter() runs: the reference adapter only when no configuration is given (None)."""
    return reference_argv() if argv is None else checked_argv(argv)


def adapter_command(argv: list[str] | None = None) -> list[str]:
    """Current generator/checker name for the same strictly selected command."""
    return adapter_argv(argv)


def adapter(
    requests: list[dict[str, Any]], argv: list[str] | None = None
) -> list[dict[str, Any]]:
    argv = adapter_argv(argv)
    lines = [canonical(r) + b"\n" for r in requests]
    if any(len(line) > 2_097_152 for line in lines):
        raise ValueError("conformance request capacity exceeded")
    output = run_adapter(argv, b"".join(lines), cwd=ROOT / "contract-lab")
    responses = [decode(line.decode("utf-8")) for line in output.splitlines()]
    assert len(responses) == len(requests), (len(responses), len(requests))
    return responses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--case-prefix",
        help="Only generate selected case IDs; retain all other pinned fixtures",
    )
    args = parser.parse_args()
    manifest = []
    failed = []
    pending = []
    all_cases = cases(args.case_prefix)
    if not all_cases:
        raise ValueError("No cases match the selected prefix")
    responses = adapter([request for case in all_cases for request in case["requests"]])
    offset = 0
    for case in all_cases:
        observed = responses[offset : offset + len(case["requests"])]
        offset += len(case["requests"])
        try:
            for assertion in case["assertions"]:
                actual = pointer(observed[assertion["response"]], assertion["pointer"])
                assert actual == assertion["equals"], (assertion, actual)
        except (AssertionError, KeyError, IndexError, TypeError) as error:
            failed.append({"id": case["id"], "error": str(error), "observed": observed})
            continue
        case["expected"] = observed
        pending.append(case)
    if failed:
        path = ROOT / "agent-runtime/evidence/production-spec-vector-failures.json"
        path.write_text(json.dumps(failed, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.write(
            str(
                json.dumps({"failed": [r["id"] for r in failed], "evidence": str(path)})
            )
            + "\n"
        )
        return 1
    if args.write:
        for case in pending:
            path = ROOT / "spec/conformance" / ("case-" + case["id"] + ".json")
            path.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n")
            manifest.append(
                {
                    "id": case["id"],
                    "path": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "requests": len(case["requests"]),
                    "requirements": case["requirements"],
                }
            )
        path = ROOT / "spec/conformance/manifest.json"
        retained = json.loads(path.read_text())
        selected = {entry["id"] for entry in manifest}
        retained["cases"] = [
            entry for entry in retained["cases"] if entry["id"] not in selected
        ] + manifest
        if not args.case_prefix or args.case_prefix.startswith("v2-"):
            name = "band-wire-examples-v2.json"
            artifact = ROOT / "spec/conformance" / name
            artifact.write_text(
                json.dumps(
                    band_wire_examples(source("hybrid-assistant")),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            entry = {
                "path": name,
                "schema": "band_wire_examples",
                "requirements": ["BAND-001", "NET-003"],
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
            retained["artifacts"] = [
                item for item in retained["artifacts"] if item["path"] != name
            ] + [entry]
        path.write_text(json.dumps(retained, indent=2) + "\n")
        index = coverage(
            json.loads((ROOT / "spec/requirements.json").read_text()), retained
        )
        (ROOT / "spec/conformance/coverage.json").write_text(
            json.dumps(index, indent=2) + "\n"
        )
    sys.stdout.write(
        str(
            json.dumps(
                {
                    "cases": len(all_cases),
                    "requests": sum(len(c["requests"]) for c in all_cases),
                    "write": args.write,
                }
            )
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
