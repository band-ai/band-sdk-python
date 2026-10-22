from __future__ import annotations

import json
import signal
import subprocess
import sys
from copy import deepcopy
from typing import Any
from unittest.mock import Mock, patch

import pytest

from remote_agent.receiptwake import WakeJournal
from remote_agent.storage import StoreError
from tests.contracts.runtime.receiptwake.kit import ReceiptWakeFixture
from tests.contracts.runtime.required import require_value


@pytest.mark.contract_linux
class TestReceiptWake(ReceiptWakeFixture):
    def test_sender_restart_retains_receipt_and_exact_wake_identity(self) -> None:
        with self.journal() as journal:
            journal.stage(self.event)
            pending = journal.pending()
        with self.journal() as journal:
            self.assertEqual(journal.pending(), pending)
            journal.stage({"status": "duplicate", "original": self.event})
            self.assertEqual(journal.pending(), pending)
            self.assertEqual(pending[0]["receipt"], self.event["id"])
            self.assertEqual(pending[0]["recipient"], "member")

    def test_lost_wake_reply_retries_same_receipt_and_receiver_dedups(self) -> None:
        receiver_path = self.path.with_name("receiver.sqlite3")
        results, sends = [], []

        def send(wake: Any) -> dict[str, Any]:
            sends.append(deepcopy(wake))
            with WakeJournal(receiver_path, self.receiver_scope()) as receiver:
                receiver.recover(Mock(inbox=Mock(return_value=self.page())))
                result = receiver.claim(wake["receipt"])
                results.append(result["status"])
                if result["status"] == "claimed":
                    receiver.complete(wake["receipt"])
            if len(sends) == 1:
                raise OSError("reply lost after receiver completion")
            return {
                "receipt": wake["receipt"],
                "recipient": "member",
                "status": "duplicate",
            }

        with self.journal() as sender:
            sender.stage(self.event)
            with self.assertRaises(OSError):
                sender.flush(send)
            self.assertEqual(len(sender.pending()), 1)
        with self.journal() as sender:
            sender.flush(send)
            self.assertEqual(sender.pending(), [])
        self.assertEqual(sends[0], sends[1])
        self.assertEqual(results, ["claimed", "duplicate"])

    def test_inbox_page_and_cursor_commit_atomically_and_resume_after_restart(
        self,
    ) -> None:
        room = Mock(inbox=Mock(return_value=self.page()))
        with self.journal(self.receiver_scope()) as receiver:
            receiver.recover(room)
        room.inbox.return_value = {"cursor": 7, "events": [], "head": {"sequence": 7}}
        with self.journal(self.receiver_scope()) as receiver:
            receiver.recover(room)
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "claimed",
            )
        self.assertEqual(room.inbox.call_args_list[-1].args, (7,))

    def test_failed_page_commit_does_not_advance_cursor_or_admit_work(self) -> None:
        with self.journal(self.receiver_scope()) as receiver:
            with (
                patch.object(
                    receiver.store, "_append", side_effect=StoreError("disk full")
                ),
                self.assertRaises(StoreError),
            ):
                receiver.recover(Mock(inbox=Mock(return_value=self.page())))
            self.assertEqual(receiver.store.get("wake_cursor", 0), 0)
            with self.assertRaises(StoreError):
                receiver.claim(self.event["id"])

    def test_process_death_after_claim_holds_work_instead_of_repeating_effect(
        self,
    ) -> None:
        with self.journal(self.receiver_scope()) as receiver:
            receiver.recover(Mock(inbox=Mock(return_value=self.page())))
        script = """import json, os, sys
from remote_agent.receiptwake import WakeJournal
with WakeJournal(sys.argv[1], json.loads(sys.argv[2])) as receiver:
    assert receiver.claim(sys.argv[3])["status"] == "claimed"
    os._exit(73)
"""
        died = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.path),
                json.dumps(self.receiver_scope()),
                self.event["id"],
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(died.returncode, 73, died.stderr)
        with self.journal(self.receiver_scope()) as receiver:
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "held",
            )
            receiver.complete(self.event["id"])
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "duplicate",
            )

    def test_conflicting_receipt_reuse_and_identity_changes_fail_closed(self) -> None:
        with self.journal() as sender:
            sender.stage(self.event)
            with self.assertRaises(StoreError):
                sender.stage(dict(self.event, payload={"task": "different"}))
        with (
            self.assertRaises(StoreError),
            self.journal(dict(self.scope, session="other-room")),
        ):
            pass

    def test_rejection_and_wrong_transport_ack_never_complete_wake(self) -> None:
        with self.journal() as sender:
            with self.assertRaises(StoreError):
                sender.stage(dict(self.event, status="rejected"))
            sender.stage(self.event)
            with self.assertRaises(StoreError):
                sender.flush(
                    lambda _: {
                        "receipt": "wrong",
                        "recipient": "member",
                        "status": "accepted",
                    }
                )
            self.assertEqual(len(sender.pending()), 1)

    def test_forged_wake_or_wrong_recipient_cannot_claim_work(self) -> None:
        with self.journal(self.receiver_scope()) as receiver:
            with self.assertRaises(StoreError):
                receiver.claim(self.event["id"])
            forged = dict(self.event, recipients=["someone-else"])
            with self.assertRaises(StoreError):
                receiver.recover(
                    Mock(inbox=Mock(return_value=dict(self.page(), events=[forged])))
                )
            self.assertEqual(receiver.store.get("wake_cursor", 0), 0)

    def test_held_work_requires_explicit_evidence_before_safe_retry(self) -> None:
        with self.journal(self.receiver_scope()) as receiver:
            receiver.recover(Mock(inbox=Mock(return_value=self.page())))
            receiver.claim(self.event["id"])
            with self.assertRaises(StoreError):
                receiver.reconcile(self.event["id"], "retry", "")
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "held",
            )
            receiver.reconcile(
                self.event["id"],
                "retry",
                "previous worker fenced; destination proves no effect",
            )
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "claimed",
            )

    def test_each_recipient_has_independent_confirmation_and_retries_do_not_reopen(
        self,
    ) -> None:
        event = dict(self.event, recipients=["member", "reviewer"])
        with self.journal() as sender:
            sender.stage(event)
            sender.confirm(event["id"], "member")
            sender.stage({"status": "duplicate", "original": event})
            self.assertEqual(
                [wake["recipient"] for wake in sender.pending()], ["reviewer"]
            )
        with self.journal() as sender:
            sender.confirm(event["id"], "reviewer")
            self.assertEqual(sender.pending(), [])

    def test_sigkill_mid_inbox_transaction_rolls_back_cursor_and_event_together(
        self,
    ) -> None:
        with self.journal(self.receiver_scope()):
            pass
        script = """import json, os, signal, sys
from unittest.mock import Mock
from remote_agent.receiptwake import WakeJournal
with WakeJournal(sys.argv[1], json.loads(sys.argv[2])) as receiver:
    append = receiver.store._append
    def kill(*args):
        append(*args)
        os.kill(os.getpid(), signal.SIGKILL)
    receiver.store._append = kill
    receiver.recover(Mock(inbox=Mock(return_value=json.loads(sys.argv[3]))))
"""
        died = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.path),
                json.dumps(self.receiver_scope()),
                json.dumps(self.page()),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(died.returncode, -signal.SIGKILL, died.stderr)
        with self.journal(self.receiver_scope()) as receiver:
            self.assertEqual(receiver.store.get("wake_cursor", 0), 0)
            self.assertEqual(receiver.store.get("wake_inbox", {}), {})
            receiver.recover(Mock(inbox=Mock(return_value=self.page())))
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "claimed",
            )

    def test_cursor_conflict_and_backward_page_preserve_pending_receipts(self) -> None:
        with self.journal(self.receiver_scope()) as receiver:
            receiver.recover(Mock(inbox=Mock(return_value=self.page())))
            room = Mock(
                inbox=Mock(side_effect=OSError("cursor ahead of restored history"))
            )
            with self.assertRaises(OSError):
                receiver.recover(room)
            with self.assertRaises(StoreError):
                receiver.recover(
                    Mock(inbox=Mock(return_value={"cursor": 6, "events": []}))
                )
            self.assertEqual(receiver.store.get("wake_cursor"), 7)
            self.assertEqual(
                require_value(
                    receiver.claim(self.event["id"]), "Expected a claimable journal job"
                )["status"],
                "claimed",
            )
