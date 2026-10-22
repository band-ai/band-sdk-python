from __future__ import annotations

import tempfile
import unittest
from typing import Any

from remote_agent.program import Artifact, Program

SOURCE = """
from remote_agent.sdk import Handled, Message, Delegate
STATE_VERSION = "1"
COVERAGE = {"mode": "partial", "handles": ["exact ping"], "delegates": ["free-form English"]}
def initial(view):
    return {"count": 0}
def handle(event, state, context):
    if event["payload"]["text"] == "ping":
        state["count"] += 1
        context.checkpoint(state)
        return Handled(state, Message("answer", {"request": event["id"], "text": "pong"}))
    return Delegate("Requires language judgment")
"""


class ProgramFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-program-")
        self.addCleanup(self.temp.cleanup)

    def program(self, source: Any = SOURCE, **options: Any) -> Any:
        return Program(Artifact.publish(source, self.temp.name), **options)
