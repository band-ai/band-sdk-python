from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from remote_agent.authority import DurableRoom
from remote_agent.network import RoomAPI
from remote_agent.storage import StoreError
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
from tests.contracts.runtime.df48rejections import RejectionAuditFixture
from tests.contracts.runtime.required import require_value


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestRejectionAudit(RejectionAuditFixture):
    def test_rejected_id_reason_and_room_time_are_durable_without_payload_or_version_bump(
        self,
    ) -> None:
        message = self.message()
        message["payload"]["private"] = "DO_NOT_AUDIT_THIS_PAYLOAD"
        before = self.room.status()
        result = self.room.publish(message, "user")
        self.assertEqual(result["status"], "rejected")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["data"],
            {
                "id": "bad",
                "actor": "user",
                "kind": "question",
                "reason": result["reason"],
                "now": self.now,
            },
        )
        self.assertNotIn("DO_NOT_AUDIT_THIS_PAYLOAD", json.dumps(rows))
        self.assertEqual(self.room.status()["operations"], before["operations"])
        self.assertEqual(self.room.view("agent")["view"]["version"], 0)
        self.assertEqual(self.room.view("agent")["view"]["facts"], {})
        self.restart()
        self.assertEqual(self.rows(), rows)
        api = RoomAPI(self.room)
        page = api.dispatch(
            "POST",
            "/v1/inbox",
            {"actor": "agent", "permissions": ["room"]},
            {"after": 0, "limit": 128},
        )[1]
        self.assertEqual(page["events"], [])
        self.assertEqual(page["cursor"], self.store.journal_head()["sequence"])

    def test_exact_rejection_retries_remain_audited_attempts_not_reserved_accepted_ids(
        self,
    ) -> None:
        bad = self.message()
        self.room.publish(bad, "user")
        first = self.rows()
        self.now += 5
        self.assertEqual(self.room.publish(bad, "user")["status"], "rejected")
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(
            self.rows()[0], first[0], "Prior audit rows must never be overwritten"
        )
        self.assertEqual(self.rows()[1]["data"]["now"], self.now)
        self.restart()
        corrected = self.message(text="corrected")
        accepted = self.room.publish(corrected, "user")
        self.assertEqual(accepted["status"], "accepted")
        count = len(self.rows())
        self.assertEqual(
            self.room.publish(corrected, "user"),
            {"status": "duplicate", "original": accepted},
        )
        self.assertEqual(len(self.rows()), count)
        conflict = self.room.publish(self.message(text="changed"), "user")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(self.rows()[-1]["data"]["reason"], conflict["reason"])
        self.restart()
        self.assertEqual(
            self.room.publish(corrected, "user"),
            {"status": "duplicate", "original": accepted},
        )

    def test_colliding_rejected_ids_preserve_each_authenticated_actor(self) -> None:
        self.room.publish(self.message(), "user")
        self.room.publish(self.message(), "agent")
        self.assertEqual(
            [row["data"]["actor"] for row in self.rows()], ["user", "agent"]
        )
        self.assertEqual(
            self.room.publish(self.message(text="valid"), "user")["status"], "accepted"
        )
        self.assertEqual(self.room.view("agent")["view"]["version"], 1)

    def test_snapshot_and_accepted_tail_replay_ignore_audit_rows(self) -> None:
        q1 = self.message(identifier="q1", text="first")
        accepted = self.room.publish(q1, "user")
        self.room.publish(self.message(version=1), "user")
        self.room.publish(
            {
                "id": "a1",
                "session": "s",
                "kind": "answer",
                "expected_version": 1,
                "payload": {"request": "q1", "text": "answer"},
            },
            "agent",
        )
        checkpoint = require_value(
            self.store.latest_snapshot(), "Expected a committed snapshot"
        )["sequence"]
        self.room.publish(self.message(identifier="bad2", version=2), "user")
        q2 = self.message(identifier="q2", text="tail", version=2)
        self.room.publish(q2, "user")
        self.room.publish(self.message(identifier="bad3", version=3), "user")
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertGreater(rows[-1]["sequence"], checkpoint)
        self.restart()
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.room.view("agent")["view"]["version"], 3)
        self.assertEqual(
            self.room.publish(q1, "user"), {"status": "duplicate", "original": accepted}
        )

    def test_failed_audit_commit_is_not_acknowledged_and_recovers_without_phantom_rows(
        self,
    ) -> None:
        with (
            patch.object(
                self.store, "commit", side_effect=StoreError("injected full disk")
            ),
            self.assertRaises(StoreError),
        ):
            self.room.publish(self.message(), "user")
        self.assertFalse(self.room.status()["ready"])
        self.restart()
        self.assertEqual(self.rows(), [])
        self.assertEqual(
            self.room.publish(self.message(), "user")["status"], "rejected"
        )
        self.assertEqual(len(self.rows()), 1)

    def test_process_death_after_audit_commit_preserves_record_without_creating_a_fact(
        self,
    ) -> None:
        self.close()
        script = """
import os,sys
from remote_agent.authority import DurableRoom
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
s=Store(sys.argv[1]); e=EngineProcess(sys.argv[2])
r=DurableRoom(s,e,session="s",bindings={"requester":"user","assistant":"agent"},
              clock=lambda:1790000000000,snapshot_every=2)
commit=s.commit
def crash(kind,*args,**kwargs):
 result=commit(kind,*args,**kwargs)
 if kind=="room_rejection": os._exit(92)
 return result
s.commit=crash
r.publish({"id":"bad","session":"s","kind":"question","expected_version":0,
           "payload":{"text":False}},"user")
e.close(); s.close()
"""
        child = subprocess.run(
            [
                sys.executable,
                "-W",
                "error",
                "-c",
                script,
                str(self.root / "room.sqlite3"),
                str(self.contract),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.start()
        self.assertEqual(child.returncode, 92, child.stderr)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.room.view("agent")["view"]["facts"], {})
        self.assertEqual(
            self.room.publish(self.message(text="valid"), "user")["status"], "accepted"
        )

    def test_invalid_metadata_shapes_do_not_copy_untrusted_objects_into_audit(
        self,
    ) -> None:
        self.room.publish(
            {
                "id": {"private": "SECRET"},
                "session": "s",
                "kind": ["SECRET"],
                "payload": {"private": "SECRET"},
            },
            "user",
        )
        self.assertEqual(len(self.rows()), 1)
        self.assertIsNone(self.rows()[0]["data"]["id"])
        self.assertIsNone(self.rows()[0]["data"]["kind"])
        self.assertNotIn("SECRET", json.dumps(self.rows()))

    def test_rejection_that_fires_expiry_replays_only_timer_without_private_payload(
        self,
    ) -> None:
        # A long snapshot interval forces the expiry operation through tail replay.
        self.close()
        path = self.root / "expiry.sqlite3"
        self.store = Store(path)
        self.engine = EngineProcess(self.contract)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            bindings={"requester": "user", "assistant": "agent"},
            clock=lambda: self.now,
            snapshot_every=20,
        )
        original = self.room.publish(
            self.message(identifier="q1", text="valid"), "user"
        )
        self.now += 300_001
        rejected = self.room.publish(
            self.message(identifier="late", text="PRIVATE_EXPIRED_PAYLOAD", version=1),
            "user",
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(len(self.rows()), 1)
        records = self.store.records()
        self.assertNotIn("PRIVATE_EXPIRED_PAYLOAD", json.dumps(records))
        self.assertEqual(
            records[-2]["data"]["request"], {"op": "tick", "now": self.now}
        )
        self.assertEqual(self.room.view("agent")["view"]["state"], "stalled")
        self.assertEqual(self.room.view("agent")["view"]["version"], 2)
        self.close()
        self.store = Store(path)
        self.engine = EngineProcess(self.contract)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            bindings={"requester": "user", "assistant": "agent"},
            clock=lambda: self.now,
            snapshot_every=20,
        )
        self.assertEqual(self.store.records(), records)
        self.assertEqual(
            self.room.publish(self.message(identifier="q1", text="valid"), "user"),
            {"status": "duplicate", "original": original},
        )
