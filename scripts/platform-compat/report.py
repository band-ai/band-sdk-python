"""Dependency-free fallback report, written before environment setup."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree

SCENARIO = "sdk-room-roundtrip"


def write_report(directory: Path, row: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    result = {"scenario_id": SCENARIO, "observed_coverage": "runtime-only", **row}
    (directory / "results.json").write_text(
        json.dumps({"scenarios": [result]}, indent=2) + "\n", encoding="utf-8"
    )
    outcome = result["outcome"]
    suite = ElementTree.Element(
        "testsuite",
        name="platform-compat",
        tests="1",
        failures=str(int(outcome == "fail")),
        errors=str(int(outcome == "incomplete")),
    )
    case = ElementTree.SubElement(suite, "testcase", name=SCENARIO)
    if outcome != "pass":
        ElementTree.SubElement(
            case,
            "failure" if outcome == "fail" else "error",
            message=str(result.get("reason", outcome)),
        )
    ElementTree.ElementTree(suite).write(
        directory / "junit.xml", encoding="utf-8", xml_declaration=True
    )


if __name__ == "__main__":
    write_report(
        Path(sys.argv[1]),
        {
            "outcome": "incomplete",
            "category": "setup",
            "reason": "Harness did not finish; inspect environment setup or process termination.",
            "cleanup": "not-started",
        },
    )
