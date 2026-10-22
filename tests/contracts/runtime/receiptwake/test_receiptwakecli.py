from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.contracts.runtime.receiptwake.kit import ReceiptWakeCLIFixture


@pytest.mark.contract_runtime
class TestReceiptWakeCLI(ReceiptWakeCLIFixture):
    def test_propose_stages_durable_wake_before_print_and_confirmation_survives_retry(
        self,
    ) -> None:
        fixture = self.fixture
        accepted = fixture.success("propose", "question", '{"text":"wake me"}')["body"]
        pending = fixture.success("wakes")["body"]["pending"]
        self.assertEqual(pending[0]["receipt"], accepted["id"])
        fixture.success("wake-confirm", accepted["id"], "room-peer")
        fixture.success("propose", "--retry")
        self.assertEqual(fixture.success("wakes")["body"]["pending"], [])

    def test_sender_dies_after_room_accept_before_local_finalize_exact_retry_recovers(
        self,
    ) -> None:
        fixture = self.fixture
        # Cut only local finalization after HTTP acceptance; the next process must
        # replay the original proposal, not manufacture another room effect.
        script = """import os, sys
from remote_agent import room_cli
save = room_cli.save
def crash(path, value):
    if path.name == "staged.json" and value.get("resolved"):
        os._exit(74)
    return save(path, value)
room_cli.save = crash
sys.exit(room_cli.main(sys.argv[1:]))
"""
        args = [
            "http://127.0.0.1:" + str(fixture.server.server_port),
            str(fixture.token_file),
            "--state-dir",
            str(fixture.root / "state"),
            "propose",
            "question",
            '{"text":"crash"}',
        ]
        died = subprocess.run(
            [sys.executable, "-c", script, *args],
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ, PYTHONPATH=str(fixture.runtime)),
            check=False,
        )
        self.assertEqual(died.returncode, 74, died.stderr)
        fixture.success("propose", "--retry")
        self.assertEqual(fixture.proposals[0], fixture.proposals[1])
        self.assertEqual(len(fixture.success("wakes")["body"]["pending"]), 1)

    def test_sender_dies_before_receipt_staging_wakes_recovers_original_proposal(
        self,
    ) -> None:
        fixture = self.fixture
        script = """import os, sys
from remote_agent import room_cli
room_cli.WakeJournal.stage = lambda *_: os._exit(75)
sys.exit(room_cli.main(sys.argv[1:]))
"""
        args = [
            "http://127.0.0.1:" + str(fixture.server.server_port),
            str(fixture.token_file),
            "--state-dir",
            str(fixture.root / "state"),
            "propose",
            "question",
            '{"text":"prestage"}',
        ]
        died = subprocess.run(
            [sys.executable, "-c", script, *args],
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ, PYTHONPATH=str(fixture.runtime)),
            check=False,
        )
        self.assertEqual(died.returncode, 75, died.stderr)
        self.assertEqual(len(fixture.success("wakes")["body"]["pending"]), 1)
        self.assertEqual(fixture.proposals[0], fixture.proposals[1])
