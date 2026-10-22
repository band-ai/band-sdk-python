"""Index portable examples per requirement without claiming acceptance or qualification."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from contracts.spec.tools.v2trace import NO_ROW, TAGS, TOOLING

# Frozen 0.1.0 artifacts stay pinned in the manifest as schema-only historical evidence
# (conformance/README.md), but they are not examples of any current requirement (spec K3).
HISTORICAL_ARTIFACTS = frozenset({"band-wire-examples.json"})


def coverage(catalog: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for requirement in catalog["requirements"]:
        identifier = requirement["id"]
        rows.append(
            {
                **{
                    key: requirement[key]
                    for key in ("id", "profiles", "specification", "acceptance")
                },
                "examples": {
                    "cases": [
                        entry["id"]
                        for entry in manifest["cases"]
                        if identifier in entry["requirements"]
                    ],
                    "artifacts": [
                        entry["path"]
                        for entry in manifest["artifacts"]
                        if identifier in entry["requirements"]
                        and entry["path"] not in HISTORICAL_ARTIFACTS
                    ],
                },
            }
        )

    def counts(selected: list[dict[str, Any]]) -> dict[str, int]:
        with_examples = sum(
            bool(row["examples"]["cases"] or row["examples"]["artifacts"])
            for row in selected
        )
        return {
            "requirements": len(selected),
            "requirements_with_examples": with_examples,
            "requirements_without_examples": len(selected) - with_examples,
        }

    profiles = sorted({profile for row in rows for profile in row["profiles"]})
    return {
        "profile": "band-contract/coverage/1",
        "revision": catalog["revision"],
        "qualification": "not_assessed",
        "summary": counts(rows),
        "profiles": {
            profile: counts([row for row in rows if profile in row["profiles"]])
            for profile in profiles
        },
        "requirements": rows,
    }


def coverage_v2(
    catalog: dict[str, Any], tags: Mapping[str, Mapping[str, tuple[str, ...]]]
) -> dict[str, Any]:
    """Index every requirements-v2.json obligation against the checks tagged in v2trace.py.

    A tagged check is evidence for an obligation, not proof of every clause; the index stays
    `not_assessed`, and obligations with no tagged check are listed as `unasserted`. Checks marked
    tooling-only, and checks of rules the catalog has no row for (by spec file), are listed apart."""

    def checks(match: Callable[[tuple[str, ...]], bool]) -> list[str]:
        return [
            f"{tool}:{name}"
            for tool, table in tags.items()
            for name, ids in table.items()
            if match(ids)
        ]

    rows: list[dict[str, Any]] = []
    for requirement in catalog["requirements"]:
        identifier = requirement["id"]
        rows.append(
            {
                **{
                    key: requirement[key]
                    for key in ("id", "level", "profiles", "specification")
                },
                "acceptance": [
                    {
                        "obligation": text,
                        "checks": checks(
                            lambda ids, identifier=identifier, n=n: (
                                f"{identifier}/{n}" in ids
                            )
                        ),
                    }
                    for n, text in enumerate(requirement["acceptance"], 1)
                ],
                "checks": checks(
                    lambda ids, identifier=identifier: any(
                        tag.partition("/")[0] == identifier for tag in ids
                    )
                ),
            }
        )
    items = [
        (row["id"], n, item)
        for row in rows
        for n, item in enumerate(row["acceptance"], 1)
    ]
    unasserted = [
        f"{identifier}/{n}" for identifier, n, item in items if not item["checks"]
    ]
    with_checks = sum(bool(row["checks"]) for row in rows)
    return {
        "profile": "band-contract/coverage/2",
        "engine": catalog["engine"],
        "qualification": "not_assessed",
        "summary": {
            "requirements": len(rows),
            "requirements_with_checks": with_checks,
            "requirements_without_checks": len(rows) - with_checks,
            "acceptance_items": len(items),
            "acceptance_items_with_checks": len(items) - len(unasserted),
            "acceptance_items_without_checks": len(unasserted),
        },
        "unasserted": unasserted,
        "tooling_only_checks": checks(lambda ids: ids == (TOOLING,)),
        "checks_without_v2_row": {
            doc: checks(lambda ids, doc=doc: ids == (NO_ROW + doc,))
            for doc in sorted(
                {
                    ids[0][len(NO_ROW) :]
                    for table in tags.values()
                    for ids in table.values()
                    if ids[0].startswith(NO_ROW)
                }
            )
        },
        "requirements": rows,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    result = coverage(
        json.loads((root / "requirements.json").read_text()),
        json.loads((root / "conformance/manifest.json").read_text()),
    )
    (root / "conformance/coverage.json").write_text(json.dumps(result, indent=2) + "\n")
    sys.stdout.write(str(json.dumps(result["summary"])) + "\n")
    result = coverage_v2(json.loads((root / "requirements-v2.json").read_text()), TAGS)
    (root / "conformance/coverage-v2.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    sys.stdout.write(str(json.dumps(result["summary"])) + "\n")
