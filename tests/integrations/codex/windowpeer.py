"""Real Windows helper that rejects startup before or after child creation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace


def main() -> int:
    stage, source, report, *args = sys.argv[1:]
    spawn = SimpleNamespace(**run_path(source))
    job_name, status_handle, command = spawn._parse(args)
    Path(report).write_text(str(os.getpid()))
    env = spawn.read_environment()
    job = spawn._open_job(job_name)
    if stage == "empty":
        spawn._write_status(status_handle, 5)
        sys.stdin.buffer.read()
    process, thread = spawn._spawn_suspended(job, command, status_handle, env)
    spawn._close_stdio()
    spawn._resume(thread)
    if stage == "child":
        spawn._write_status(status_handle, 5)
    return spawn._wait_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
