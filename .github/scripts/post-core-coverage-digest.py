#!/usr/bin/env python3
"""Post a compact weekly digest for a Core consumer-coverage artifact."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOW_COVERAGE_PERCENT = 80.0


@dataclass(frozen=True)
class FileCoverage:
    path: str
    found: int
    hit: int

    @property
    def percent(self) -> float:
        return 100 * self.hit / self.found if self.found else 100.0

    @property
    def missed(self) -> int:
        return self.found - self.hit


def display_path(path: str) -> str:
    marker = "/crates/"
    return path[path.index(marker) + 1 :] if marker in path else Path(path).name


def parse_lcov(path: Path) -> list[FileCoverage]:
    records: list[FileCoverage] = []
    source: str | None = None
    found = hit = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            source = line[3:]
        elif line.startswith("LF:"):
            found = int(line[3:])
        elif line.startswith("LH:"):
            hit = int(line[3:])
        elif line == "end_of_record" and source is not None:
            records.append(FileCoverage(display_path(source), found, hit))
            source = None
            found = hit = 0
    return records


def render_digest(
    *, lcov_path: Path, label: str, recipients: str, run_url: str, result: str
) -> str:
    status = "PASS" if result == "success" else result.upper()
    header = f"## Weekly Core coverage: {status}"
    if not lcov_path.is_file():
        return "\n".join(
            [
                header,
                recipients,
                "",
                "No LCOV report was produced. See the failed run for details.",
                "",
                f"[Open run]({run_url})",
            ]
        )

    files = parse_lcov(lcov_path)
    found = sum(item.found for item in files)
    hit = sum(item.hit for item in files)
    percent = 100 * hit / found if found else 0.0
    gaps = sorted(
        (item for item in files if item.percent < LOW_COVERAGE_PERCENT),
        key=lambda item: (item.percent, -item.found, item.path),
    )
    lines = [
        header,
        recipients,
        "",
        f"**{label}: {percent:.2f}% lines** ({hit}/{found}). Low coverage is below {LOW_COVERAGE_PERCENT:.0f}%.",
        "",
    ]
    if gaps:
        lines.extend(
            [
                "### Low or uncovered files",
                "",
                "| File | Lines | Missed |",
                "| --- | ---: | ---: |",
            ]
        )
        lines.extend(
            f"| `{item.path}` | {item.percent:.2f}% | {item.missed}/{item.found} |"
            for item in gaps
        )
    else:
        lines.append("All measured files meet the coverage floor.")
    lines.extend(["", f"[Open run and coverage artifact]({run_url}#artifacts)"])
    return "\n".join(lines)


def main() -> None:
    digest = render_digest(
        lcov_path=Path(os.environ["LCOV_PATH"]),
        label=os.environ["REPORT_LABEL"],
        recipients=os.environ["RECIPIENTS"],
        run_url=os.environ["RUN_URL"],
        result=os.environ["WORKFLOW_RESULT"],
    )
    subprocess.run(
        [
            "gh",
            "api",
            f"repos/{os.environ['REPO']}/commits/{os.environ['SHA']}/comments",
            "--method",
            "POST",
            "-f",
            f"body={digest}",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
