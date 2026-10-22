from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from remote_agent.authority import RoomClock
from remote_agent.storage import StoreError
from remote_agent.transport import (
    EngineProcess,
    LocalGate,
    TransportError,
    reference_room_policy,
)
from tests.contracts.runtime.durableroom.kit import DurableRoomFixture
from tests.contracts.runtime.required import require_value


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestDurableRoom(DurableRoomFixture):
    def test_whole_authority_restart_preserves_facts_and_the_original_duplicate_receipt(
        self,
    ) -> None:
        accepted = self.question()
        self.restart()
        repeated = self.room.publish(self.message, "user", self.clock)
        self.assertEqual(repeated, {"status": "duplicate", "original": accepted})
        self.assertEqual(
            self.room.view("agent", self.clock)["view"]["facts"]["question-1"][
                "payload"
            ],
            self.message["payload"],
        )

    def test_offline_time_does_not_restart_deadlines(self) -> None:
        self.question()
        self.clock += 300_001
        self.restart()
        self.assertEqual(
            self.room.view("agent", self.clock)["view"]["status"], "terminal"
        )

    def test_storage_failure_cannot_acknowledge_or_retain_uncommitted_room_acceptance(
        self,
    ) -> None:
        with (
            patch.object(
                self.store, "commit", side_effect=StoreError("injected full disk")
            ),
            self.assertRaises(StoreError),
        ):
            self.question()
        self.restart()
        self.assertEqual(self.room.view("agent", self.clock)["view"]["version"], 0)
        self.assertEqual(self.question()["status"], "accepted")

    def test_changed_bindings_cannot_rebind_a_persisted_session(self) -> None:
        self.question()
        with self.assertRaisesRegex(TransportError, "configuration"):
            self.restart(bindings={"requester": "stranger", "assistant": "agent"})

    def test_rejected_message_does_not_become_an_accepted_fact_after_restart(
        self,
    ) -> None:
        self.message = {
            "id": "bad",
            "session": "s",
            "kind": "answer",
            "payload": {"request": "absent", "text": "no"},
            "expected_version": 0,
        }
        self.assertEqual(
            self.room.publish(self.message, "agent", self.clock)["status"], "rejected"
        )
        self.restart()
        self.assertNotIn("bad", self.room.view("user", self.clock)["view"]["facts"])

    def test_room_time_is_authoritative_and_local_preflight_uses_that_clock(
        self,
    ) -> None:
        self.question()
        self.clock += 1
        projection = self.room.view("agent", 0)
        self.assertEqual(projection["now"], self.clock)
        checker = EngineProcess(self.contract)
        self.engines.append(checker)
        answer = {
            "id": "answer-1",
            "session": "s",
            "kind": "answer",
            "expected_version": 1,
            "payload": {"request": "question-1", "text": "checked"},
        }
        decision = LocalGate(checker).check(
            projection, answer, "agent", 999_999_999_999_999
        )
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(self.room.publish(answer, "agent", 0), decision)

    def test_crash_after_database_commit_before_receipt_delivery_keeps_acceptance(
        self,
    ) -> None:
        self.engine.close()
        self.store.close()
        script = """
import os,sys
from remote_agent.authority import DurableRoom
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
s=Store(sys.argv[1]); e=EngineProcess(sys.argv[2])
r=DurableRoom(s,e,session="s",bindings={"requester":"user","assistant":"agent"},
              clock=lambda:1790000000000,snapshot_every=2)
commit=s.commit
def crash_after_commit(kind,*args,**kwargs):
 result=commit(kind,*args,**kwargs)
 if kind=="room_operation": os._exit(91)
 return result
s.commit=crash_after_commit
r.publish({"id":"question-1","session":"s","kind":"question","expected_version":0,
           "payload":{"text":"private accepted input"}},"user")
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
        self.assertEqual(child.returncode, 91, child.stderr)
        self.start()
        result = self.question()
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(self.room.view("agent")["view"]["version"], 1)

    def test_snapshot_plus_tail_recovery_and_idle_polling_have_bounded_storage_growth(
        self,
    ) -> None:
        self.question()
        self.room.publish(
            {
                "id": "a1",
                "session": "s",
                "kind": "answer",
                "expected_version": 1,
                "payload": {"request": "question-1", "text": "accepted"},
            },
            "agent",
        )
        checkpoint_position = require_value(
            self.store.latest_snapshot(), "Expected a committed snapshot"
        )["sequence"]
        self.room.publish(
            {
                "id": "q2",
                "session": "s",
                "kind": "question",
                "expected_version": 2,
                "payload": {"text": "after the checkpoint"},
            },
            "user",
        )
        records = len(self.store.records())
        for _ in range(50):
            self.clock += 1
            self.room.tick()
            self.room.view("agent")
        self.assertEqual(len(self.store.records()), records)
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["sequence"],
            checkpoint_position,
        )
        self.restart()
        self.assertEqual(self.room.view("agent")["view"]["version"], 3)
        self.assertEqual(len(self.room.view("agent")["view"]["facts"]), 3)

    def test_member_operations_carry_a_host_policy_listing_every_bound_actor(
        self,
    ) -> None:
        with patch.object(self.engine, "request", wraps=self.engine.request) as spy:
            self.assertEqual(self.question()["status"], "accepted")
            self.room.view("agent")
            self.room.tick()
        sent = {call.kwargs["op"]: call.kwargs for call in spy.call_args_list}
        member = {"kind": "agent", "active": True, "permission": "participant"}
        policy = sent["step"]["room_policy"]
        # The caller is "user"; the policy still lists the other bound actor.
        self.assertEqual(policy["principals"], {"user": member, "agent": member})
        self.assertEqual(policy["fixed_roles"], {})
        self.assertTrue(isinstance(policy["revision"], str) and policy["revision"])
        self.assertEqual(sent["view"]["room_policy"], policy)
        for op in ("tick", "summary"):
            self.assertNotIn("room_policy", sent[op])
        journal = [
            frame["data"]["request"]
            for frame in self.store.records()
            if frame["kind"] == "room_operation"
        ]
        self.assertEqual(journal[-1]["room_policy"], policy)

    def test_hosted_endpoint_refuses_member_operations_without_host_policy(
        self,
    ) -> None:
        bindings = {"requester": "user", "assistant": "agent"}
        start = {
            "op": "new",
            "session": "direct",
            "bindings": bindings,
            "now": self.clock,
            "inactivity": "accepted",
            "max_messages": 100,
            "capabilities": ["contract.replies/1"],
        }
        for extra, reason in (
            ({}, "host_policy_required"),
            ({"room_policy": {"mode": "unchecked"}}, "invalid_room_policy"),
        ):
            engine = EngineProcess(self.contract)
            self.engines.append(engine)
            with self.assertRaisesRegex(TransportError, "^" + reason + "$"):
                engine.request(**start, **extra)
        engine = EngineProcess(self.contract)
        self.engines.append(engine)
        engine.request(**start, room_policy=reference_room_policy(bindings))
        message = {
            "id": "q",
            "session": "direct",
            "kind": "question",
            "payload": {"text": "hi"},
            "expected_version": 0,
        }
        step = {"op": "step", "message": message, "actor": "user", "now": self.clock}
        self.assertEqual(
            engine.request(**step, room_policy=reference_room_policy(bindings))[
                "status"
            ],
            "accepted",
        )
        # An exact retry without host evidence is refused, not answered as a duplicate.
        self.assertEqual(
            engine.request(**step),
            {"status": "rejected", "reason": "host_policy_required"},
        )
        with self.assertRaisesRegex(TransportError, "^host_policy_required$"):
            engine.request(op="view", actor="user", now=self.clock)

    def test_host_wall_clock_regression_keeps_elapsed_time_including_submillisecond_calls(
        self,
    ) -> None:
        with (
            patch("remote_agent.authority.time.time_ns", return_value=1_000_000_000),
            patch("remote_agent.authority.time.monotonic_ns", return_value=0),
        ):
            clock = RoomClock()
        observed: int | None = None
        with patch("remote_agent.authority.time.time_ns", return_value=0):
            for step in range(1, 11):
                with patch(
                    "remote_agent.authority.time.monotonic_ns",
                    return_value=step * 100_000,
                ):
                    observed = clock()
            self.assertEqual(observed, 1001)
