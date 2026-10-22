from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.band.arithmetic import validate_candidate

SOURCE = """from remote_agent.sdk import Delegate, Handled, Message
STATE_VERSION = "normalization/1"
COVERAGE = {"mode":"full", "handles":["bounded ASCII normalization"], "delegates":["outside the documented input"]}
def initial(view):
    return {"processed":0,"mode":"ascii"}
def handle(event,state,context):
    if context.view.get("status") == "terminal":
        return Handled(state)
    payload = event.get("payload", {})
    if (event.get("kind") != "normalize" or set(payload) != {"text"}
        or not isinstance(payload["text"],str) or not payload["text"].isascii()
        or not 1 <= len(payload["text"].encode("utf-8")) <= 80):
        return Delegate("Not a bounded ASCII normalization input")
    next_state = {"processed":state["processed"] + 1,"mode":"ascii"}
    context.checkpoint(next_state)
    return Handled(next_state,Message("normalized",{"request":event["id"],"text":payload["text"].upper()}))
"""


def policy() -> dict[str, Any]:
    state = {"processed": 0, "mode": "ascii"}
    request = {
        "id": "normalization-1",
        "session": "qualification",
        "kind": "normalize",
        "actor": "Agent:requester",
        "role": "requester",
        "payload": {"text": "  Band 42!  "},
    }
    active = {"status": "active", "state": "normalizing"}
    result = {
        "type": "handled",
        "state": {"processed": 1, "mode": "ascii"},
        "message": {
            "kind": "normalized",
            "payload": {"request": request["id"], "text": "  BAND 42!  "},
        },
    }
    cases: list[dict[str, Any]] = [
        {
            "name": "uppercase preserves spacing and punctuation",
            "group": "normalize",
            "event": request,
            "state": state,
            "view": active,
            "expected": result,
            "allow_delegate": True,
        }
    ]
    for name, event in (
        (
            "non-ASCII needs judgment outside this role coverage",
            dict(request, payload={"text": "é"}),
        ),
        ("empty input is not guessed", dict(request, payload={"text": ""})),
        (
            "extra instructions do not alter the structured task",
            dict(request, payload={"text": "Band", "instruction": "lowercase"}),
        ),
        ("other messages delegate", dict(request, kind="rewrite")),
    ):
        cases.append(
            {
                "name": name,
                "group": None,
                "event": event,
                "state": state,
                "view": active,
                "expected": {"type": "delegate"},
                "allow_delegate": False,
            }
        )
    cases.append(
        {
            "name": "terminal input causes no state change or reply",
            "group": None,
            "event": request,
            "state": {"processed": 4, "mode": "ascii"},
            "view": {"status": "terminal"},
            "expected": {
                "type": "handled",
                "state": {"processed": 4, "mode": "ascii"},
                "message": None,
            },
            "allow_delegate": False,
        }
    )
    return {
        "profile": "band-contract/program-quality/1",
        "source_sha256": "b" * 64,
        "role": "normalizer",
        "state_version": "normalization/1",
        "initial": state,
        "modes": ["full", "partial"],
        "groups": ["normalize"],
        "cases": cases,
    }


class RoleQualityFixture(unittest.TestCase):
    def qualify(
        self, source: Any = SOURCE, qualification: Any = None, **scope: Any
    ) -> Any:
        with tempfile.TemporaryDirectory() as directory:
            return validate_candidate(
                source,
                Path(directory),
                qualification=qualification or policy(),
                role=scope.get("role", "normalizer"),
                contract_sha256=scope.get("contract_sha256", "b" * 64),
            )
