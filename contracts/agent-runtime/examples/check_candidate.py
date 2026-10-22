"""Host-owned semantic checks. A model response cannot replace this executable."""

from __future__ import annotations

import json
import sys

from remote_agent.program import Artifact, Program

request = json.load(sys.stdin)
candidate = Program(Artifact(**request["candidate"]))
assert candidate.describe({})["schema"] == "1"
for text, expected in [("ping", "pong"), ("recover", "recovered")]:
    event = {"id": "fixture-q", "kind": "question", "payload": {"text": text}}
    result = candidate.handle(event, {"count": 8}, {})
    assert result["type"] == "handled"
    assert result["state"] == {"count": 9}
    assert result["message"] == {
        "kind": "answer",
        "payload": {"request": "fixture-q", "text": expected},
    }
unknown = candidate.handle(
    {"kind": "question", "payload": {"text": "Interpret arbitrary English as JSON"}},
    {"count": 8},
    {},
)
assert unknown["type"] == "delegate"
sys.stdout.write(
    json.dumps(
        {
            "ok": True,
            "checks": [
                "exact ping answer and count",
                "exact recovery answer and count",
                "arbitrary English remains delegated",
            ],
        }
    )
    + "\n"
)
