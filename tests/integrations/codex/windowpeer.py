"""Real Windows helper that rejects startup before or after child creation."""

from __future__ import annotations

import sys

from band.integrations.codex.winspawn import (
    _close_stdio,
    _open_job,
    _parse,
    _resume,
    _spawn_suspended,
    _wait_process,
    _write_status,
)


def main() -> int:
    stage, *args = sys.argv[1:]
    job_name, status_handle, command = _parse(args)
    job = _open_job(job_name)
    if stage == "empty":
        _write_status(status_handle, 5)
        sys.stdin.buffer.read()
    process, thread = _spawn_suspended(job, command, status_handle)
    _close_stdio()
    _resume(thread)
    if stage == "child":
        _write_status(status_handle, 5)
    return _wait_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
