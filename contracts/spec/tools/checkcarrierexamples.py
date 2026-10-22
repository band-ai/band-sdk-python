"""Compile and execute carrier examples; preserve actual child exits and doc consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT

logger = logging.getLogger(__name__)

DOC = ROOT / "spec/carrier-examples.md"
MAPS = ROOT / "spec/examples/carriers.json"
EXAMPLE = ROOT / "contract-lab/examples/carrier_round_trip.exs"


def check_docs() -> list[str]:
    maps = json.loads(MAPS.read_text())
    blocks = re.findall(r"```json\n(.*?)\n```", DOC.read_text(), re.DOTALL)
    assert [json.loads(block) for block in blocks] == maps, (
        "Doc maps differ from executable input"
    )
    for path in (DOC, ROOT / "spec/conformance/README.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            file_name, _, fragment = target.partition("#")
            linked = (path.parent / file_name).resolve()
            assert linked.is_file(), f"Missing link: {path.name}: {target}"
            if fragment and linked.suffix == ".md":
                headings = re.findall(r"^#+ (.+)$", linked.read_text(), re.MULTILINE)
                anchors = {
                    re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                    for heading in headings
                }
                assert fragment in anchors, f"Missing anchor: {path.name}: {target}"
    return ["doc-map-parity", "local-file-links", "local-heading-links"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="JSON receipt; command logs are saved beside it"
    )
    args = parser.parse_args()
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                DOC,
                MAPS,
                EXAMPLE,
                Path(__file__).resolve(),
                ROOT / "spec/conformance/README.md",
                ROOT / "contract-lab/contracts/v2/one-review.band",
            )
        },
        "commands": [],
        "checks": [],
        "passed": False,
        "qualification": "local examples only; no live transport or deployment",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ("header-spikes", ["mix", "deps.get", "--check-locked"]),
        ("header-spikes", ["mix", "compile", "--warnings-as-errors"]),
        ("contract-lab", ["mix", "deps.get", "--check-locked"]),
        ("contract-lab", ["mix", "compile", "--warnings-as-errors"]),
        (
            "contract-lab",
            [
                "mix",
                "contract.check",
                "--format",
                "frontmatter",
                "contracts/v2/one-review.band",
            ],
        ),
        ("contract-lab", ["mix", "run", "examples/carrier_round_trip.exs"]),
        (
            "contract-lab",
            ["mix", "format", "--check-formatted", "examples/carrier_round_trip.exs"],
        ),
        (".", ["git", "diff", "--check", "HEAD"]),
    ]
    try:
        report["checks"] = check_docs()
        for index, (relative, argv) in enumerate(commands, 1):
            start = time.monotonic()
            entry = {"cwd": relative, "argv": argv, "returncode": None}
            report["commands"].append(entry)
            try:
                child = subprocess.run(
                    argv,
                    cwd=ROOT / relative,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                entry["returncode"] = child.returncode
                output = child.stdout + child.stderr
            except subprocess.TimeoutExpired:
                entry["timed_out"] = True
                raise RuntimeError(f"Command exceeded 120 seconds: {argv}")
            finally:
                entry["elapsed_seconds"] = round(time.monotonic() - start, 3)
            if args.output:
                log = args.output.with_name(f"{args.output.stem}-{index:02d}.log")
                log.write_text(output)
                entry["log"] = str(log)
            sys.stdout.write(str(f"exit {child.returncode}: {' '.join(argv)}") + "\n")
            if child.returncode != 0:
                raise RuntimeError(output[-6000:])
            if argv == ["mix", "run", "examples/carrier_round_trip.exs"]:
                runtime = json.loads(child.stdout.strip().splitlines()[-1])
                assert runtime["passed"] is True
                report["runtime"] = runtime
        report["passed"] = True
    except Exception as error:
        logger.debug("Specification check failed", exc_info=True)
        report["error"] = str(error)
        sys.stderr.write(str(str(error)) + "\n")
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["exit_code"] = 0 if report["passed"] else 1
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(
        str(
            json.dumps(
                {
                    "passed": report["passed"],
                    "exit_code": report["exit_code"],
                    "commands_run": len(report["commands"]),
                    "checks": report["checks"],
                }
            )
        )
        + "\n"
    )
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
