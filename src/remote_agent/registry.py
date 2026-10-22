"""Register callback sessions with the trusted qualification owner, when present."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from remote_agent.capabilities import require_linux
from remote_agent.settings import runner_settings

PROCESS_LOG = "CONTRACT_QUALIFICATION_PROCESS_LOG"


def register_process(process: subprocess.Popen[bytes]) -> None:
    # The registry belongs to the host, never the callback's allowlisted env.
    path = runner_settings().contract_qualification_process_log
    if not path:
        return
    require_linux()
    try:
        fields = (
            Path("/proc", str(process.pid), "stat")
            .read_text()
            .rsplit(") ", 1)[1]
            .split()
        )
    except FileNotFoundError:
        started = None
    else:
        if int(fields[2]) != process.pid:
            raise ValueError("Qualification child must own its process group")
        started = int(fields[19])
    # Match contracts.deploy.qualificationprocess's PID/start-time journal format.
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.write(
            descriptor, (json.dumps([process.pid, started]) + "\n").encode("utf-8")
        )
    finally:
        os.close(descriptor)
