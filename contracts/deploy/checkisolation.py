"""Check real callback isolation before expensive release qualification."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# Direct isolated invocation trusts only this checkout's package roots.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from remote_agent.program import Artifact, Program, ProgramError

SOURCE = """
from remote_agent.sdk import Handled
STATE_VERSION = "1"
COVERAGE = {"mode": "full", "handles": ["qualification probe"], "delegates": []}
def initial(view): return {"steps": 0}
def handle(event, state, context): return Handled({"steps": state["steps"] + 1})
"""


def probe() -> dict[str, Any]:
    report = {"profile": "band-contract/isolation-preflight/1", "passed": False}
    with tempfile.TemporaryDirectory(
        prefix="contract-isolation-preflight-"
    ) as directory:
        program = Program(Artifact.publish(SOURCE, Path(directory) / "artifacts"))
        for operation, payload in (
            ("describe", {"view": {}}),
            ("handle", {"event": {}, "state": {"steps": 0}, "view": {}}),
        ):
            execution = program.execute(program.request(operation, **payload))
            try:
                result = program.result(execution, op=operation)
            except ProgramError as error:
                # This is only our fixed synthetic probe, never an agent callback.
                # Retain the launcher's bounded diagnostic without exposing runtime
                # callback stderr through the user-facing error boundary.
                return dict(
                    report,
                    operation=operation,
                    exit_code=execution["exit_code"],
                    error=str(error),
                    stderr=base64.b64decode(execution["stderr"]).decode(
                        "utf-8", errors="replace"
                    )[:2000],
                )
            state = result["initial"] if operation == "describe" else result["state"]
            if state != {"steps": 0 if operation == "describe" else 1}:
                return dict(
                    report,
                    operation=operation,
                    error="Unexpected qualification probe result",
                )
    return dict(report, passed=True)


def main() -> int:
    report = probe()
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
