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
    child_code = (
        "import ctypes,pathlib; sleep=ctypes.WinDLL('kernel32').Sleep; "
        "sleep.argtypes=[ctypes.c_ulong]; sleep.restype=None; "
        f"pathlib.Path({str(ready.resolve())!r}).touch(); sleep(60000)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code], stdin=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "known sleeping child did not initialize"
        with (ARTIFACTS / "known-sleep-capture.txt").open("wb") as output:
            result = subprocess.run(
                [
                    str(ARTIFACTS / "procdump64.exe"),
                    "-accepteula",
                    "-mm",
                    "-at",
                    "3",
                    str(child.pid),
                    str(dump),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=10,
            )
        (ARTIFACTS / "known-sleep-returncode.txt").write_text(str(result.returncode))
        assert result.returncode in {0, 1}, "ProcDump returned an unexpected status"
        assert dump.is_file() and dump.read_bytes()[:4] == b"MDMP", (
            "ProcDump did not produce a valid dump"
        )
    finally:
        child.terminate()
        child.wait(timeout=5)
    symbols = (ARTIFACTS / "symbols").resolve()
    symbols.mkdir(parents=True, exist_ok=True)
    with report.open("wb") as output:
        subprocess.run(
            [
                str(CDB),
                "-z",
                str(dump),
                "-y",
                f"srv*{symbols}*https://msdl.microsoft.com/download/symbols",
                "-i",
                f"C:/Windows/System32;{sys.base_prefix}",
                "-c",
                ".reload /f;~*kb;lm;q",
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
