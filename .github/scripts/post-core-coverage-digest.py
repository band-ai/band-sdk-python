#!/usr/bin/env python3
"""Post a compact weekly digest for a Core consumer-coverage artifact."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOW_COVERAGE_PERCENT = 80.0
MAX_MISSED_LINE_RANGES = 8
MAX_LOW_COVERAGE_FILES = 8


@dataclass(frozen=True)
class FileCoverage:
    path: str
    found: int
    hit: int
    functions_found: int
    functions_hit: int
    missed_lines: tuple[int, ...]

    @property
    def percent(self) -> float:
        return 100 * self.hit / self.found if self.found else 100.0

    @property
    def missed(self) -> int:
        return self.found - self.hit


def display_path(path: str) -> str:
    marker = "/crates/"
    return path[path.index(marker) + 1 :] if marker in path else Path(path).name


def format_line_ranges(numbers: tuple[int, ...]) -> str:
    ranges: list[str] = []
    start = previous = None
    for number in numbers:
        if start is None:
            start = previous = number
        elif number == previous + 1:
            previous = number
        else:
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = number
    if start is not None:
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
    shown = ", ".join(ranges[:MAX_MISSED_LINE_RANGES])
    return (
        shown
        if len(ranges) <= MAX_MISSED_LINE_RANGES
        else f"{shown}, … ({len(numbers)} missed)"
    )


def coverage_marker(percent: float) -> str:
    if percent >= LOW_COVERAGE_PERCENT:
        return "🟢"
    if percent >= 50:
        return "🟠"
    return "🔴"


def parse_lcov(path: Path) -> list[FileCoverage]:
    records: list[FileCoverage] = []
    source: str | None = None
    found = hit = functions_found = functions_hit = 0
    missed_lines: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            source = line[3:]
        elif line.startswith("LF:"):
            found = int(line[3:])
        elif line.startswith("LH:"):
            hit = int(line[3:])
        elif line.startswith("FNF:"):
            functions_found = int(line[4:])
        elif line.startswith("FNH:"):
            functions_hit = int(line[4:])
        elif line.startswith("DA:"):
            line_number, count = line[3:].split(",", maxsplit=1)
            if count == "0":
                missed_lines.append(int(line_number))
        elif line == "end_of_record" and source is not None:
            records.append(
                FileCoverage(
                    display_path(source),
                    found,
                    hit,
                    functions_found,
                    functions_hit,
                    tuple(missed_lines),
                )
            )
            source = None
            found = hit = functions_found = functions_hit = 0
            missed_lines = []
    return records


def render_digest(
    *, lcov_path: Path, label: str, recipients: str, run_url: str, result: str
) -> str:
    header = "## 📊 Weekly Core coverage"
    if not lcov_path.is_file():
        return "\n".join(
            [
                header,
                recipients,
                "",
                f"⚠️ **Coverage unavailable** · workflow `{result}`",
                "",
                "No LCOV report was produced. Open the run for the failure details.",
                "",
                f"[Open run]({run_url})",
            ]
        )

    files = parse_lcov(lcov_path)
    found = sum(item.found for item in files)
    hit = sum(item.hit for item in files)
    functions_found = sum(item.functions_found for item in files)
    functions_hit = sum(item.functions_hit for item in files)
    gaps = sorted(
        (item for item in files if item.percent < LOW_COVERAGE_PERCENT),
        key=lambda item: (item.percent, -item.found, item.path),
    )
    line_percent = 100 * hit / found if found else 0.0
    function_percent = 100 * functions_hit / functions_found if functions_found else 0.0
    healthy_files = len(files) - len(gaps)
    lines = [
        header,
        "",
        recipients,
        "",
        f"**{label}**",
        "",
        "### Coverage snapshot",
        "",
        "| Signal | Result |",
        "| --- | --- |",
        f"| Lines | {coverage_marker(line_percent)} **{line_percent:.2f}%** · {hit}/{found} covered · {found - hit} missing |",
        f"| Functions | {coverage_marker(function_percent)} **{function_percent:.2f}%** · {functions_hit}/{functions_found} covered · {functions_found - functions_hit} missing |",
        f"| Files at target | {coverage_marker(100 * healthy_files / len(files) if files else 0)} **{healthy_files}/{len(files)}** at or above {LOW_COVERAGE_PERCENT:.0f}% |",
        "",
    ]
    if gaps:
        lines.extend(
            [
                "### 🎯 Where to focus",
                "",
                "| Source file | Coverage gap |",
                "| --- | --- |",
            ]
        )
        lines.extend(
            f"| `{item.path}` | {coverage_marker(item.percent)} **{item.percent:.2f}%** · {item.missed} lines missing<br>Lines `{format_line_ranges(item.missed_lines)}` |"
            for item in gaps[:MAX_LOW_COVERAGE_FILES]
        )
        if len(gaps) > MAX_LOW_COVERAGE_FILES:
            lines.extend(
                ["", f"_Plus {len(gaps) - MAX_LOW_COVERAGE_FILES} more low-coverage files in the artifact._"]
            )
    else:
        lines.append("✅ Every measured source file meets the coverage target.")
    lines.extend(["", f"[View the run and full coverage artifact →]({run_url}#artifacts)"])
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
