from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from typing import Any

import pytest

from tests.contracts.runtime.service import ServiceFixture
from tests.paths import CONTRACT_RUNTIME_ROOT


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestRoomCLILive(unittest.TestCase):
    def test_real_serve_acceptance_duplicate_and_durable_cursor(self) -> None:
        # Use the service's actual fixture/launcher without inheriting its unrelated tests.
        fixture = ServiceFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        token = fixture.private("cli-user-token", "u" * 32)
        _, url = fixture.launch(fixture.room_config(), "cli-room")
        payload = fixture.private("question.json", {"text": "DF-01 live"})
        command = [
            sys.executable,
            "-m",
            "remote_agent.room_cli",
            url,
            token,
            "--state-dir",
            str(fixture.root / "cli-state"),
        ]

        def invoke(*args: Any) -> Any:
            result = subprocess.run(
                command + list(args),
                capture_output=True,
                text=True,
                timeout=15,
                env=dict(os.environ, PYTHONPATH=str(CONTRACT_RUNTIME_ROOT.parent)),
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return json.loads(result.stdout)["body"]

        accepted = invoke("propose", "question", payload)
        self.assertEqual(accepted["status"], "accepted")
        self.assertRegex(accepted["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            invoke("propose", "--retry"), {"status": "duplicate", "original": accepted}
        )
        page = invoke("inbox")
        self.assertGreater(page["cursor"], 0)
        self.assertEqual(invoke("inbox", "--peek"), page)
        invoke("inbox", "--ack", str(page["cursor"]))
        self.assertEqual(invoke("inbox")["events"], [])
