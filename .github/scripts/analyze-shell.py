"""Export captured Windows process timelines and native thread stacks."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
ARTIFACTS = Path("artifacts/shell-diagnostic")
WINDOWS_KITS = Path("C:/Program Files (x86)/Windows Kits/10")


def run_report(args: list[str], output: Path) -> None:
    with output.open("wb") as stream:
        try:
            result = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=90,
                check=False,
            )
            logger.info(
                "Report finished: returncode=%s output=%s", result.returncode, output
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.error("Report unavailable: output=%s error=%s", output, error)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    debuggers = list(WINDOWS_KITS.glob("Debuggers/x64/cdb.exe"))
    exporters = list(WINDOWS_KITS.glob("**/xperf.exe"))
    for dump in ARTIFACTS.glob("*.dmp"):
        if debuggers:
            run_report(
                [
                    str(debuggers[0]),
                    "-z",
                    str(dump),
                    "-y",
                    "srv*artifacts/shell-diagnostic/symbols*https://msdl.microsoft.com/download/symbols",
                    "-c",
                    ".reload;~*kb;lm;q",
                ],
                dump.with_suffix(".stacks.txt"),
            )
        else:
            logger.warning(
                "CDB is absent; native stack analysis requires SDK Debugging Tools"
            )
    for trace in ARTIFACTS.glob("shell-timeout.etl"):
        if exporters:
            run_report(
                [str(exporters[0]), "-i", str(trace), "-a", "process"],
                trace.with_suffix(".processes.txt"),
            )
            run_report(
                [str(exporters[0]), "-i", str(trace), "-symbols", "-a", "dumper"],
                trace.with_suffix(".events.txt"),
            )
        else:
            logger.warning(
                "Xperf is absent; ETL analysis requires SDK Windows Performance Toolkit"
            )


if __name__ == "__main__":
    main()
