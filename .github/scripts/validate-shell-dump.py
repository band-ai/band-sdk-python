"""Prove native stack capture after the original cold shell scenario."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
ARTIFACTS = Path("artifacts/shell-diagnostic")
CDB = Path("C:/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    dump = ARTIFACTS / "known-sleep.dmp"
    report = ARTIFACTS / "known-sleep.stacks.txt"
    ready = ARTIFACTS / "known-sleep.ready"
    child_code = f"import pathlib,time; pathlib.Path({str(ready.resolve())!r}).touch(); time.sleep(60)"
    child = subprocess.Popen(
        [sys.executable, "-c", child_code], stdin=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "known sleeping child did not initialize"
        with (ARTIFACTS / "known-sleep-capture.txt").open("wb") as output:
            subprocess.run(
                [
                    str(ARTIFACTS / "procdump64.exe"),
                    "-accepteula",
                    "-mt",
                    "-at",
                    "3",
                    str(child.pid),
                    str(dump),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=10,
            )
    finally:
        child.terminate()
        child.wait(timeout=5)
    with report.open("wb") as output:
        subprocess.run(
            [
                str(CDB),
                "-z",
                str(dump),
                "-y",
                "srv*artifacts/shell-diagnostic/symbols*https://msdl.microsoft.com/download/symbols",
                "-c",
                ".reload;~*kb;lm;q",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=90,
        )
    stacks = report.read_text(errors="replace")
    assert "NtDelayExecution" in stacks or "SleepEx" in stacks, (
        "known sleeping child has no resolved native sleep stack"
    )
    logger.info(
        "Native dump stack validated for known sleeping child: pid=%s report=%s",
        child.pid,
        report,
    )


if __name__ == "__main__":
    main()
