"""Synthetic model fixture for exercising the command boundary; no live model."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

request = json.load(sys.stdin)
operation, bundle = request["operation"], request["bundle"]
workspace = Path(sys.argv[1])
if operation == "respond":
    event = bundle["input"]
    text = {"ping": "pong", "recover": "recovered"}.get(
        event["payload"]["text"], "A scripted answer standing in for model judgment."
    )
    result = {
        "state": {"count": bundle["state"]["count"] + 1},
        "scope": "turn",
        "message": {
            "kind": "answer",
            "payload": {"request": event["id"], "text": text},
        },
    }
elif operation == "wake":
    result = {
        "action": "none",
        "note": "Inspected the journal and current role view (scripted fixture).",
    }
elif operation == "repair":
    (workspace / "repair-started").touch()
    deadline = time.monotonic() + 15
    while not (workspace / "release-repair").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Demo did not release background repair")
        time.sleep(0.02)
    result = {
        "source": request["program_source"].replace(
            'Message("question",', 'Message("answer",'
        ),
        "resume": True,
    }
else:
    raise ValueError("Unknown fixture operation")
sys.stdout.write(json.dumps(result) + "\n")
