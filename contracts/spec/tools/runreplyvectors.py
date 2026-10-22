"""Run the reply-alternative conformance vectors (replyvectors.py) and the old-fixture stability pins.

  runreplyvectors.py [--adapter-config FILE] [--case-prefix P] [--json-out FILE] [--table FILE]
                       [--stability] [--write-pins]

Each case runs in its own adapter process, so a compile failure can never leave an earlier
case's contract behind its handles. A `{"$from": [i, pointer]}` value in a request or an
assertion is the observed value of response i of the same case (checkpoint packets).

Prompt cases (replyvectors.prompt_cases, lead decision 6) run `mix contract.check --require-prompts`
and remote_agent.band_format.missing_prompts on each source and compare both with the hand-written list.

Stability (DESIGN §5, V6: old contracts keep digest and restore shape) compiles the BASE bytes
of every fixture from a digest-pinned source archive, so later fixture edits cannot hide a change,
starts a session, checkpoints, restores and checkpoints again. It compares the digest, the
normalized compiled object, the new-session state and the checkpoint with the pins written on
the base, and requires the restored checkpoint to equal the original.

Prints one JSON summary line; exits 0 only when every selected case passes.
The source archive preserves the original revision independently of the SDK repository history.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from contracts.spec.tools import replyvectors
from contracts.spec.tools.generatevectors import adapter, checked_argv, pointer
from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT
from contracts.spec.tools.vectorinputs import hosted
from remote_agent.band_format import missing_prompts
from remote_agent.protocol import canonical
from tests.contracts.runtime.formatter import (
    PROMPTS_SCRIPT,
    elixir_outputs,
    python_outputs,
)

logger = logging.getLogger(__name__)

PINS = ROOT / "spec/conformance/reply-stability-pins.json"
PIN_PROFILE = "band-contract/reply-stability-pins/1"
STABILITY_SOURCES = ROOT / "spec/conformance/reply-stability-sources.json"
STABILITY_SOURCES_SHA256 = (
    "4535e7082a6a4b6473572c7c49d94d3b6c5365b4b01b9f57b3e52c033b6093f7"
)
STABILITY_BASE = "76648af"


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def resolve(value: Any, observed: list[Any] | None) -> Any:
    if isinstance(value, dict) and set(value) == {"$from"}:
        index, path = value["$from"]
        if observed is None:
            return None
        try:  # an earlier failure (RED) leaves nothing to copy; the case then fails on its assertions
            return copy.deepcopy(pointer(observed[index], path))
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(value, dict):
        return {k: resolve(v, observed) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, observed) for v in value]
    return value


def has_from(value: Any) -> bool:
    if isinstance(value, dict):
        return "$from" in value or any(has_from(v) for v in value.values())
    if isinstance(value, list):
        return any(has_from(v) for v in value)
    return False


def run_case(case: dict[str, Any], argv: list[str] | None) -> dict[str, Any]:
    requests = case["requests"]
    observed = None
    if has_from(
        requests
    ):  # phase 1: the prefix up to the first placeholder supplies its values
        first = next(i for i, r in enumerate(requests) if has_from(r))
        observed = adapter(hosted(copy.deepcopy(requests[:first])), argv)
    observed = adapter(
        hosted(resolve(requests, observed and observed + [None] * len(requests))), argv
    )
    failures = []
    for a in case["assertions"]:
        want = resolve(a["equals"], observed)
        try:
            got = pointer(observed[a["response"]], a["pointer"])
        except (KeyError, IndexError, TypeError):
            got = "<absent>"
        if got != want:
            failures.append(
                {
                    "response": a["response"],
                    "pointer": a["pointer"],
                    "expected": want,
                    "actual": got
                    if len(json.dumps(got)) <= 300
                    else json.dumps(got)[:300] + "…",
                }
            )
    return {
        "id": case["id"],
        "status": "pass" if not failures else "fail",
        "assertions": len(case["assertions"]),
        "failed_assertions": len(failures),
        "first_failures": failures[:3],
    }


def run_format(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Both formatters, through the parity harness of tests/contracts/runtime/formatter.py."""
    module = str(ROOT / "contract-lab/lib/contract_lab/format.ex")
    data = [case["input"].encode() for case in cases]

    def decoded(outputs: list[dict[str, Any]]) -> list[str | dict[str, Any]]:
        return [
            base64.b64decode(o["ok"]).decode() if "ok" in o else {"error": o["error"]}
            for o in outputs
        ]

    ex1 = decoded(elixir_outputs(module, data))
    py1 = decoded(python_outputs(data))
    ex2 = decoded(
        elixir_outputs(module, [x.encode() if isinstance(x, str) else b"" for x in ex1])
    )
    py2 = decoded(
        python_outputs([x.encode() if isinstance(x, str) else b"" for x in py1])
    )
    results = []
    for case, e1, e2, p1, p2 in zip(cases, ex1, ex2, py1, py2):
        checks = {
            "elixir_once_is_expected": e1 == case["expected"],
            "elixir_twice_is_once": e2 == e1,
            "python_once_is_expected": p1 == case["expected"],
            "python_twice_is_once": p2 == p1,
            "elixir_equals_python": e1 == p1,
        }
        failed = [name for name, ok in checks.items() if not ok]
        results.append(
            {
                "id": case["id"],
                "status": "pass" if not failed else "fail",
                "assertions": len(checks),
                "failed_assertions": len(failed),
                "first_failures": failed,
            }
        )
    return results


def run_prompts(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """--require-prompts on both sides: `mix contract.check --require-prompts` (the PROMPTS_SCRIPT of
    tests/contracts/runtime/formatter.py) and remote_agent.band_format.missing_prompts."""
    with tempfile.TemporaryDirectory(prefix="reply-prompts-") as tmp:
        files = []
        for i, case in enumerate(cases):
            path = Path(tmp) / f"case{i}.band"
            path.write_text(case["input"])
            files.append(path)
        batch, script = Path(tmp) / "files.json", Path(tmp) / "prompts.exs"
        batch.write_text(json.dumps([str(p) for p in files]))
        script.write_text(PROMPTS_SCRIPT)
        run = subprocess.run(
            ["mix", "run", "--no-start", str(script), str(batch)],
            capture_output=True,
            text=True,
            timeout=900,
            cwd=ROOT / "contract-lab",
            check=False,
        )
        try:
            elixir = json.loads(run.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            elixir = [{"error": run.stderr[-300:]}] * len(cases)
        results = []
        for case, path, ex in zip(cases, files, elixir):
            try:
                py = missing_prompts(path.read_bytes())
            except Exception as error:  # a RED Python finder may raise on U lines
                logger.debug("Specification check failed", exc_info=True)
                py = {"error": repr(error)[:200]}
            got_ex = ex.get("missing", ex)
            checks = {
                "elixir_is_expected": got_ex == case["missing"],
                "python_is_expected": py == case["missing"],
                "elixir_equals_python": got_ex == py,
            }
            failed = [name for name, ok in checks.items() if not ok]
            results.append(
                {
                    "id": case["id"],
                    "status": "pass" if not failed else "fail",
                    "assertions": len(checks),
                    "failed_assertions": len(failed),
                    "first_failures": failed,
                    "observed": {"elixir": got_ex, "python": py} if failed else None,
                }
            )
    return results


def table(cases: list[dict[str, Any]]) -> str:
    out = ["| Case | Expected (hand-written) | Notes |", "|---|---|---|"]
    for case in cases:
        shown = []
        for a in case["assertions"]:
            value = json.dumps(a["equals"], ensure_ascii=False)
            shown.append(
                f"r{a['response']} `{a['pointer']}` = `{value if len(value) < 60 else value[:57] + '…'}`"
            )
        out.append(f"| {case['id']} | {'<br>'.join(shown)} | {case.get('notes', '')} |")
    return "\n".join(out) + "\n"


# ---- stability ----------------------------------------------------------------------------


def fixtures(base: str) -> list[dict[str, Any]]:
    """Read the exact historical sources bound to the retained stability pins."""
    data = STABILITY_SOURCES.read_bytes()
    assert hashlib.sha256(data).hexdigest() == STABILITY_SOURCES_SHA256, (
        "Historical stability sources changed"
    )
    archive = json.loads(data)
    assert archive["profile"] == "band-contract/reply-stability-sources/1"
    assert archive["base"] == base, "Stability source revision differs from the pins"
    rows = archive["fixtures"]
    assert len({row["path"] for row in rows}) == len(rows), (
        "Duplicate stability source paths"
    )
    for row in rows:
        source = row["source"]
        assert (
            hashlib.sha256(source["content"].encode()).hexdigest() == source["sha256"]
        ), row["path"]
    return rows


def member(ptype: dict[str, Any], i: int) -> dict[str, Any]:
    attributes = {}
    for name, field in ptype.get("fields", {}).items():
        kind = field.get("kind")
        if kind == "enum":
            attributes[name] = field["values"][0]
        elif kind == "boolean":
            attributes[name] = False
        elif kind == "text":
            attributes[name] = "a" * max(1, field.get("bytes", {}).get("min") or 0)
        else:
            attributes[name] = field.get("min", 0)
    return {"name": f"m{i}", "actor": f"member-{i}", "attributes": attributes}


def bindings(compiled: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for role in compiled["roles"]:
        roster = (compiled.get("role_sets") or {}).get(role)
        if roster:
            ptype = compiled["participant_types"][roster["member"]]
            result[role] = [
                member(ptype, 2 * len(result)),
                member(ptype, 2 * len(result) + 1),
            ]
        else:
            result[role] = f"{role}-actor"
    return result


def stability_observe(base: str, argv: list[str] | None) -> list[dict[str, Any]]:
    rows = fixtures(base)
    compiled = adapter(
        [
            {"op": "compile", "handle": f"c{i}", "source": r["source"]}
            for i, r in enumerate(rows)
        ],
        argv,
    )
    requests, index = [], []
    for i, (row, out) in enumerate(zip(rows, compiled)):
        row["compile"] = out
        if "ok" not in out:
            continue
        index.append(i)
        requests += [
            {"op": "compile", "handle": f"c{i}", "source": row["source"]},
            {
                "op": "new",
                "contract": f"c{i}",
                "handle": f"s{i}",
                "config": {
                    "session": f"s{i}",
                    "bindings": bindings(out["ok"]),
                    "now": 0,
                    "max_messages": 100,
                    "max_bytes": 16384,
                },
            },
            {"op": "checkpoint", "handle": f"s{i}"},
        ]
    first = adapter(hosted(requests), argv)
    second_requests = []
    for n, i in enumerate(index):
        new, packet = first[3 * n + 1], first[3 * n + 2]
        rows[i]["new"], rows[i]["checkpoint"] = new, packet
        if "ok" in packet:
            second_requests += [
                requests[3 * n],
                {
                    "op": "restore",
                    "contract": f"c{i}",
                    "handle": f"r{i}",
                    "checkpoint": packet["ok"],
                    "now": 0,
                },
                {"op": "checkpoint", "handle": f"r{i}"},
            ]
    second = adapter(hosted(second_requests), argv) if second_requests else []
    restored = iter(range(0, len(second), 3))
    for i in index:
        if "ok" in rows[i]["checkpoint"]:
            k = next(restored)
            rows[i]["roundtrip"] = (
                second[k + 2] == rows[i]["checkpoint"] and "ok" in second[k + 1]
            )
    result = []
    for row in rows:
        out = row["compile"]
        entry = {"path": row["path"], "source_sha256": row["source"]["sha256"]}
        if "ok" in out:
            entry.update(
                {
                    "digest": out["ok"]["digest"],
                    "compiled_sha256": sha(out["ok"]),
                    "new_sha256": sha(row["new"]),
                    "checkpoint_sha256": sha(row["checkpoint"]),
                    "roundtrip": row.get("roundtrip", "n/a: no checkpoint"),
                }
            )
        else:
            entry["compile_error"] = out
        result.append(entry)
    return result


def stability(argv: list[str] | None, write: bool) -> dict[str, Any]:
    if write:
        if PINS.exists():
            raise SystemExit(
                f"{PINS} exists: pins are written once, on the base, and never regenerated"
            )
        base = STABILITY_BASE
        rows = stability_observe(base, argv)
        PINS.write_text(
            json.dumps(
                {"profile": PIN_PROFILE, "base": base, "fixtures": rows}, indent=2
            )
            + "\n"
        )
        return {"pins_written": len(rows), "base": base}
    pins = json.loads(PINS.read_text())
    assert pins["profile"] == PIN_PROFILE
    observed = {row["path"]: row for row in stability_observe(pins["base"], argv)}
    failed = []
    for pin in pins["fixtures"]:
        now = observed.get(pin["path"])
        diffs = [
            key
            for key in pin
            if key != "roundtrip" and (now or {}).get(key) != pin[key]
        ]
        if now and now.get("roundtrip") not in (True, "n/a: no checkpoint"):
            diffs.append("roundtrip")
        if diffs:
            failed.append({"path": pin["path"], "differs": diffs})
    return {
        "fixtures": len(pins["fixtures"]),
        "base": pins["base"],
        "failed": failed,
        "roundtrips": sum(
            1 for row in observed.values() if row.get("roundtrip") is True
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--adapter-config", type=Path)
    parser.add_argument("--case-prefix", default="")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--table", type=Path)
    parser.add_argument(
        "--stability", action="store_true", help="also check the old-fixture pins"
    )
    parser.add_argument(
        "--write-pins", action="store_true", help="write the pins on the base (once)"
    )
    args = parser.parse_args()
    argv = (
        checked_argv(json.loads(args.adapter_config.read_text()))
        if args.adapter_config
        else None
    )
    if args.write_pins:
        sys.stdout.write(str(json.dumps(stability(argv, True))) + "\n")
        return 0
    cases = [
        c
        for c in replyvectors.cases()
        if c["id"].startswith("v2-reply-" + args.case_prefix)
    ]
    results = [run_case(case, argv) for case in cases]
    formats = [
        c
        for c in replyvectors.format_cases()
        if c["id"].startswith("v2-reply-" + args.case_prefix)
    ]
    if formats:
        results += run_format(formats)
    prompts = [
        c
        for c in replyvectors.prompt_cases()
        if c["id"].startswith("v2-reply-" + args.case_prefix)
    ]
    if prompts:
        results += run_prompts(prompts)
    if args.table:
        args.table.write_text(
            table(cases)
            + "".join(
                f"| {f['id']} | elixir and python: once == expected, twice == once, elixir == python | {f['notes']} |\n"
                for f in formats
            )
            + "".join(
                f"| {p['id']} | --require-prompts missing = `{json.dumps(p['missing'])}` (mix and python) | {p['notes']} |\n"
                for p in prompts
            )
        )
    report: dict[str, Any] = {
        "passed": sum(r["status"] == "pass" for r in results),
        "failed": sum(r["status"] == "fail" for r in results),
        "cases": results,
    }
    if args.stability:
        report["stability"] = stability(argv, False)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
    stable = not args.stability or not report["stability"]["failed"]
    sys.stdout.write(
        str(
            json.dumps(
                {
                    "passed": report["passed"],
                    "failed": report["failed"],
                    "total": len(results),
                    "stability_failed": len(report["stability"]["failed"])
                    if args.stability
                    else None,
                }
            )
        )
        + "\n"
    )
    return 0 if report["failed"] == 0 and stable else 1


if __name__ == "__main__":
    sys.exit(main())
