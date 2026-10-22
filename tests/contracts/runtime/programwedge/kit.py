from __future__ import annotations

import json
from typing import Any

import tests.contracts.runtime.program as program_tests
from tests.contracts.runtime import hybrid


def raw_handle(value: Any) -> Any:
    encoded = json.dumps(value, separators=(",", ":")).encode()
    return (
        program_tests.SOURCE
        + "\ndef handle(event, state, context):\n"
        + ("    import os\n    os.write(1, " + repr(encoded) + ")\n    os._exit(0)\n")
    )


BAD_HANDLED = {"type": "handled", "state": "not-an-object", "message": None}


BAD_DELEGATE = {"type": "delegate", "scope": "turn", "reason": "bypass"}


VALID_DESCRIBE = {
    "schema": "1",
    "coverage": {"mode": "partial", "handles": ["ping"], "delegates": ["judgment"]},
    "initial": {"count": 0},
}


def raw_describe(value: Any) -> Any:
    # Exit while loading the artifact, before any worker-side ABI check.
    encoded = json.dumps(value, separators=(",", ":")).encode()
    return "import os\nos.write(1, " + repr(encoded) + ")\nos._exit(0)\n"


class ProgramWedgeFixture(hybrid.HybridFixture):
    def check_raw_reply(self, value: Any) -> None:
        agent = hybrid.HybridFixture.start(self, raw_handle(value), max_repairs=0)
        event = self.question()
        result = agent.receive(event)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["producer"], "llm")
        self.assertIsNone(self.store.get("execution"))
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.store.get("mode"), "llm")
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertIn("program_failure", self.provider.bundles[0]["fault"]["reason"])
        outputs = [
            r["data"] for r in self.store.records() if r["kind"] == "program_output"
        ]
        self.assertEqual(outputs[-1]["execution"]["exit_code"], 0)
        self.assertIsNone(outputs[-1]["execution"]["failure"])
        self.assertTrue(outputs[-1]["execution"]["complete"])
        self.assertEqual(agent.recover()["status"], "idle")
