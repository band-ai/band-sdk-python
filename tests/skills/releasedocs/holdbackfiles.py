"""Kit: the holdbacks command's input files and what it wrote."""

from __future__ import annotations

import json
from pathlib import Path

from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import (
    HELD_PR,
    LEDGER,
    PREVIOUS_TAG,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASE_URL,
)


def release_prs(
    breaking: tuple[str, ...] = (), loose_commits: tuple[str, ...] = ()
) -> dict[str, object]:
    """Three PRs, the second the ledger's; ``loose_commits`` are commit SHAs."""
    numbers = ["101", HELD_PR, "102"]
    return {
        "tag": RELEASE_TAG,
        "previous_tag": PREVIOUS_TAG,
        "release_url": RELEASE_URL,
        "release_date": RELEASE_DATE,
        "pull_requests": [
            {
                "number": int(number),
                "sha": number * 3,
                "title": f"feat: change {number}",
                "body": "",
                "labels": [],
                "files": ["src/band/a.py"],
                "type": "feat",
                "breaking": number in breaking,
            }
            for number in numbers
        ],
        "loose_commits": [
            {"sha": sha, "subject": "fix: direct", "files": [], "breaking": False}
            for sha in loose_commits
        ],
        "excluded_internal": [],
    }


class Inputs:
    """The holdbacks command's input files in one directory."""

    def __init__(self, root: Path, notes_json: str) -> None:
        self.root = root
        self.notes = root / "release-notes.json"
        self.prs = root / "release-prs.json"
        self.ledger = root / "held-items.yml"
        self.report = root / "report.json"
        self.out = root / "bundle"
        self.outputs = root / "outputs"
        self.notes.write_text(notes_json, encoding="utf-8")
        self.write(release_prs(), LEDGER, {"held": [], "hold_breaking": False})

    def write(
        self, prs: dict[str, object], ledger: str, report: dict[str, object]
    ) -> None:
        self.prs.write_text(json.dumps(prs), encoding="utf-8")
        self.ledger.write_text(ledger, encoding="utf-8")
        self.report.write_text(json.dumps(report), encoding="utf-8")

    def argv(self) -> list[str]:
        return [
            str(self.notes),
            str(self.prs),
            "--ledger",
            str(self.ledger),
            "--report",
            str(self.report),
            "--out",
            str(self.out),
            "--outputs",
            str(self.outputs),
        ]

    def outputs_written(self) -> dict[str, str]:
        return read_outputs(self.outputs)

    def kept_labels(self) -> list[str]:
        notes = json.loads((self.out / "release-notes.json").read_text("utf-8"))
        return [item["label"] for item in notes["items"]]
