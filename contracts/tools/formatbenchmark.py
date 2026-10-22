"""Opt-in formatter timing gates; no timing assertions in ordinary unit tests.

Run from the repository: python -m contracts.tools.formatbenchmark --output result.json
--source loads an immutable band_format.py, without changing the active worktree.
Inputs and independent expected bytes are built outside the measured interval.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import platform
import signal
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any, TypeAlias

from remote_agent import band_format

logger = logging.getLogger(__name__)

Expected: TypeAlias = bytes | list[dict[str, str | int]]
Workload: TypeAlias = tuple[str, bytes, Expected]

HEADER = b"---\nname: timing\nroles: []\n---\n"


def workloads(size: int) -> dict[str, Workload]:
    plain = "a" * size
    sigils = "~r/x/" * (size // 5)
    escapes = "\\n" * (size // 2)
    unicode_text = "é" * (size // 2)
    operators = "x|y&z " * (size // 6)
    count = max(1, (size - 32) // 8)
    opening = b"```band\ntype a\n"
    content = b"v:x\n" * count
    closing = b"```" + b" " * (size - len(opening) - len(content) - 4) + b"\n"
    return {
        "plain": (
            "format_bytes",
            ("type a\nv:" + plain).encode(),
            ("type a\n  v: " + plain + "\n").encode(),
        ),
        "unicode": (
            "format_bytes",
            ("type a\nv:" + unicode_text).encode(),
            ("type a\n  v: " + unicode_text + "\n").encode(),
        ),
        "sigils": (
            "format_bytes",
            ("type a\nv:" + sigils).encode(),
            ("type a\n  v: " + sigils + "\n").encode(),
        ),
        "operators": (
            "format_bytes",
            ("state s\n" + operators).encode(),
            ("state s\n  " + "x | y & z " * (size // 6)).rstrip().encode() + b"\n",
        ),
        "escapes": (
            "missing_prompts",
            HEADER + ('"' + escapes + '"\ntype a\n').encode(),
            [
                {"item": "contract", "name": "timing", "line": 1, "file_line": 5},
                {"item": "type", "name": "a", "line": 1, "file_line": 5},
            ],
        ),
        "prompt_plain": (
            "missing_prompts",
            HEADER + plain.encode(),
            [{"item": "contract", "name": "timing", "line": 1, "file_line": 5}],
        ),
        "wait_head": (
            "missing_prompts",
            HEADER + ("a->b:m(" + plain + ") awaits w\n=>r()&done\n").encode(),
            [
                {"item": "contract", "name": "timing", "line": 1, "file_line": 5},
                {"item": "state", "name": "w", "line": 1, "file_line": 5},
            ],
        ),
        "wait_suffix_failure": (
            "missing_prompts",
            HEADER + ("a->b:m()" + " " * size + "!\n=>r()&done\n").encode(),
            [{"item": "contract", "name": "timing", "line": 1, "file_line": 5}],
        ),
        "roster_failure": (
            "format_bytes",
            ("a" * size + " : not_list").encode(),
            ("  " + "a" * size + ": not_list\n").encode(),
        ),
        "markdown": (
            "format_markdown_bytes",
            ("```band\ntype a\nv:" + plain + "\n```\n").encode(),
            ("```band\ntype a\n  v: " + plain + "\n```\n").encode(),
        ),
        "fence_failure": (
            "format_markdown_bytes",
            (" " * size + "x\n").encode(),
            (" " * size + "x\n").encode(),
        ),
        "markdown_closing_space": (
            "format_markdown_bytes",
            opening + content + closing,
            opening + b"  v: x\n" * count + closing,
        ),
        "many_lines": (
            "format_bytes",
            b"type a\n" + b"v:x\n" * (size // 4),
            b"type a\n" + b"  v: x\n" * (size // 4),
        ),
    }


def timed(
    function: Callable[[bytes], Expected], data: bytes, timeout: float
) -> tuple[float, Expected]:
    def expired(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        start = time.perf_counter_ns()
        result = function(data)
        return (time.perf_counter_ns() - start) / 1_000_000, result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run(
    module: ModuleType,
    sizes: list[int],
    repetitions: int,
    timeout: float,
    selected: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    failed = False
    for size in sizes:
        for name, (method, data, expected) in workloads(size).items():
            if selected and name not in selected:
                continue
            fn = getattr(module, method)
            row: dict[str, Any] = {
                "construct": name,
                "payload_bytes": size,
                "input_bytes": len(data),
                "method": method,
                "input_sha256": hashlib.sha256(data).hexdigest(),
            }
            row["scope"] = (
                "file"
                if name in ("many_lines", "markdown_closing_space", "prompt_types")
                else "line"
            )
            samples = []
            try:
                # Semantic smoke and warmup, then clean untraced repetitions.
                _, actual = timed(fn, data, timeout)
                if actual != expected:
                    raise AssertionError(
                        f"independent expected bytes differ: {name}/{size}"
                    )
                for _ in range(repetitions):
                    elapsed, actual = timed(fn, data, timeout)
                    if actual != expected:
                        raise AssertionError("unstable semantic result")
                    samples.append(elapsed)
            except TimeoutError:
                row.update(timeout_ms=timeout * 1000, passed=False)
                failed = True
            else:
                median = statistics.median(samples)
                budget = 250 if size == 250_000 and row["scope"] == "line" else 1000
                row.update(
                    samples_ms=samples,
                    median_ms=median,
                    min_ms=min(samples),
                    max_ms=max(samples),
                    budget_ms=budget,
                    passed=median < budget,
                )
                failed |= not row["passed"]
            rows.append(row)
    for row in rows:
        comparator = "prompt_plain" if row["method"] == "missing_prompts" else "plain"
        plain = next(
            (
                r
                for r in rows
                if r["construct"] == comparator
                and r["payload_bytes"] == row["payload_bytes"]
            ),
            None,
        )
        if (
            row["payload_bytes"] == 250_000
            and row["scope"] == "line"
            and plain
            and "median_ms" in row
            and "median_ms" in plain
        ):
            row["evil_plain_ratio"] = row["median_ms"] / plain["median_ms"]
            row["relative_budget_ms"] = min(250, max(50, 10 * plain["median_ms"]))
            row["passed"] &= row["median_ms"] <= row["relative_budget_ms"]
            failed |= not row["passed"]
        smaller = next(
            (
                r
                for r in rows
                if r["construct"] == row["construct"]
                and r["payload_bytes"] * 2 == row["payload_bytes"]
            ),
            None,
        )
        if smaller and "median_ms" in smaller and "median_ms" in row:
            row["doubling_ratio"] = row["median_ms"] / smaller["median_ms"]
            row["passed"] &= row["doubling_ratio"] <= 2.5
            failed |= not row["passed"]
    return rows, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sizes", default="125000,250000,500000,1000000")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--cases", default="")
    args = parser.parse_args()
    module: ModuleType = band_format
    if args.source:
        spec = importlib.util.spec_from_file_location(
            "remote_agent._timing_baseline", args.source
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load formatter source: {args.source}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    rows, failed = run(
        module,
        [int(n) for n in args.sizes.split(",")],
        args.repetitions,
        args.timeout,
        set(filter(None, args.cases.split(","))),
    )
    if module.__file__ is None:
        raise ValueError("Formatter module has no source path")
    result = {
        "source": str(Path(module.__file__).resolve()),
        "source_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "repetitions": args.repetitions,
        "loadavg": os.getloadavg(),
        "rows": rows,
        "passed": not failed,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for row in rows:
        logger.info(
            "%s %s %.3f ms %s",
            row["construct"],
            row["payload_bytes"],
            row.get("median_ms", row.get("timeout_ms")),
            "PASS" if row["passed"] else "FAIL",
        )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
