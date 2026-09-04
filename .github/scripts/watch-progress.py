#!/usr/bin/env python3
"""Run a command and fail it if its safe progress signal stops advancing."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import TextIO


_PROGRESS_PREFIX = "E2E_PROGRESS nodeid="
_WATCHDOG_EXIT_CODE = 124


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle-seconds", type=float, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.idle_seconds <= 0:
        parser.error("--idle-seconds must be positive")
    if not args.command or args.command[0] != "--" or len(args.command) == 1:
        parser.error("a command must follow --")
    args.command = args.command[1:]
    return args


def _read_lines(stream: TextIO, lines: Queue[str | None]) -> None:
    for line in iter(stream.readline, ""):
        lines.put(line)
    lines.put(None)


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/pid", str(process.pid), "/t", "/f"],
            check=False,
            capture_output=True,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _write_timeout_diagnostic(
    path: Path, *, nodeid: str | None, elapsed_seconds: float, pid: int
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "kind": "e2e_progress_timeout",
                "elapsed_seconds": round(elapsed_seconds, 1),
                "nodeid": nodeid,
                "pid": pid,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _arguments()
    process = subprocess.Popen(
        args.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=os.name != "nt",
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        ),
    )
    assert process.stdout is not None
    lines: Queue[str | None] = Queue()
    Thread(target=_read_lines, args=(process.stdout, lines), daemon=True).start()

    last_progress = time.monotonic()
    nodeid: str | None = None
    stream_closed = False
    while not stream_closed:
        try:
            line = lines.get(timeout=min(1.0, args.idle_seconds))
        except Empty:
            elapsed = time.monotonic() - last_progress
            if elapsed < args.idle_seconds:
                continue
            _write_timeout_diagnostic(
                args.diagnostic,
                nodeid=nodeid,
                elapsed_seconds=elapsed,
                pid=process.pid,
            )
            sys.stderr.write(
                "::error::E2E made no progress for "
                f"{elapsed:.0f}s (current node: {nodeid or 'unknown'}); terminating it\n"
            )
            sys.stderr.flush()
            _terminate_tree(process)
            return _WATCHDOG_EXIT_CODE
        if line is None:
            stream_closed = True
            continue
        sys.stdout.write(line)
        sys.stdout.flush()
        last_progress = time.monotonic()
        if line.startswith(_PROGRESS_PREFIX):
            nodeid = line.removeprefix(_PROGRESS_PREFIX).strip()

    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
