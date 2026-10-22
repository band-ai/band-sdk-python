from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, cast

from remote_agent.invocations import InvocationJournal
from remote_agent.program import Program
from remote_agent.store import Store


class InterruptedHost(BaseException):
    pass


class ProbeRetryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="probe-retry-")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(os.path.join(self.temp.name, "state.sqlite3"))
        self.addCleanup(self.store.close)
        self.journal = InvocationJournal(self.store)
        self.success = {
            "exit_code": 0,
            "failure": None,
            "complete": True,
            "stdout": base64.b64encode(
                json.dumps(
                    {
                        "schema": "1",
                        "initial": {},
                        "coverage": {"mode": "full", "handles": [], "delegates": []},
                    }
                ).encode()
            ).decode(),
            "stderr": "",
            "elapsed_ms": 1,
        }
        self.program = cast(
            Program,
            SimpleNamespace(
                artifact=SimpleNamespace(to_dict=lambda: {"sha256": "a" * 64}),
                timeout=1,
                execute=lambda *a, **kw: self.success,
            ),
        )
        self.request = {"op": "describe", "view": {}}

    def inputs(self) -> list[Any]:
        return [r["data"] for r in self.store.records() if r["kind"] == "program_input"]
