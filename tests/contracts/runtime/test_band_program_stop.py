from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from tests.contracts.runtime.bandprogramstop import ProgramStopFixture


@pytest.mark.contract_linux
class TestProgramStop(ProgramStopFixture):
    def test_stop_acceptance_commits_intent_before_effects_and_closure(self) -> None:
        host = self.host()
        self.start(host)
        participant = self.factories[0]
        original_control = participant.control

        def control(actor: Any, body: Any) -> Any:
            with sqlite3.connect(self.root / "registry.sqlite3") as reader:
                self.assertEqual(
                    reader.execute("SELECT status FROM runtimes").fetchone()[0],
                    "stopping",
                )
            return original_control(actor, body)

        participant.control = control
        view = host.operation(self.stop())
        self.assertEqual(view["runtime"]["status"], "stopping")
        self.assertEqual(view["control"]["status"], "queued")
        self.assertEqual(participant.closes, 0)
        self.assertTrue(host.step())
        self.assertEqual(
            host.db.execute("SELECT status,hold FROM runtimes").fetchone(),
            ("stopped", None),
        )
        self.assertEqual(
            host.db.execute("SELECT status FROM operations WHERE id='stop'").fetchone()[
                0
            ],
            "complete",
        )
        self.assertEqual(participant.closes, 1)
        self.assertEqual(host.instances, {})

    def test_owner_stop_is_accepted_after_the_operation_budget_is_spent(
        self,
    ) -> None:
        host = self.host()
        self.start(host)
        participant = self.factories[0]
        with host.db:
            host.db.executemany(
                "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (f"finished-{index}", "other", "other", "User:other", "task")
                    + ("fingerprint", "{}", "complete", "{}", index)
                    for index in range(host.maximum * 16)
                ],
            )
        view = host.operation(self.stop())
        self.assertEqual(view["runtime"]["status"], "stopping")
        self.assertTrue(host.step())
        self.assertEqual(participant.closes, 1)
        self.assertEqual(host.instances, {})
        with self.assertRaisesRegex(ValueError, "operation_capacity"):
            host.operation(self.request("control", id="second", action="stop"))

    def test_failed_close_keeps_barrier_and_exact_retry_uses_the_original_instance(
        self,
    ) -> None:
        host = self.host()
        self.start(host)
        participant = self.factories[0]
        participant.fail_close = True
        host.operation(self.stop())
        host.step()
        view = host.operation(self.request("status"))
        self.assertEqual(view["runtime"]["status"], "stopping")
        self.assertTrue(view["runtime"]["hold"])
        self.assertEqual(view["control"]["status"], "held")
        self.assertEqual(host.instances["room/session"], participant)
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            host.operation(self.request("control", id="stop", action="resume"))
        host.operation(self.stop())
        self.assertTrue(host.step())
        self.assertEqual(
            host.db.execute("SELECT status,hold FROM runtimes").fetchone(),
            ("stopped", None),
        )
        self.assertEqual(len(self.factories), 1)
        self.assertEqual(participant.closes, 2)
        host.operation(self.stop())
        self.assertFalse(host.step())

    def test_legacy_pending_stop_prevents_recovery_and_keeps_unknown_outcome(
        self,
    ) -> None:
        for stop_status in ("queued", "running", "held"):
            with self.subTest(stop_status=stop_status):
                root = self.root / stop_status
                host = self.host(root)
                start = self.start(host)
                stop = self.stop()
                host.operation(stop)
                # Reproduce old journals that accepted stop without a barrier.
                with host.db:
                    host.db.execute("UPDATE runtimes SET status='running',hold=NULL")
                    host.db.execute(
                        "UPDATE operations SET status=?,result=? WHERE id='stop'",
                        (
                            stop_status,
                            json.dumps({"error": "legacy_unknown"})
                            if stop_status == "held"
                            else None,
                        ),
                    )
                saved_input = host.db.execute(
                    "SELECT input FROM operations WHERE id='stop'"
                ).fetchone()[0]
                host.close()
                host = self.host(
                    root,
                    factory=lambda *_: self.fail(
                        "Stop recovery cannot start a participant"
                    ),
                )
                self.assertEqual(
                    host.db.execute("SELECT status FROM runtimes").fetchone()[0],
                    "stopping",
                )
                self.assertFalse(host.step())
                first = host.operation(stop)
                self.assertEqual(first["control"]["status"], "held")
                self.assertTrue(first["control"]["error"])
                self.assertTrue(first["runtime"]["hold"])
                host.operation(start)
                self.assertEqual(host.operation(stop), first)
                self.assertFalse(host.step())
                self.assertEqual(
                    host.db.execute(
                        "SELECT input FROM operations WHERE id='stop'"
                    ).fetchone()[0],
                    saved_input,
                )
                host.close()
                host = self.host(
                    root,
                    factory=lambda *_: self.fail(
                        "Repeated restart cannot cross stop intent"
                    ),
                )
                self.assertEqual(host.operation(stop), first)
                self.assertFalse(host.step())

    def test_stop_barrier_refuses_new_work_and_blocks_already_queued_work(self) -> None:
        host = self.host()
        self.start(host)
        task = self.request("task", id="task", kind="work", payload={})
        host.operation(task)
        host.operation(self.stop())
        with self.assertRaisesRegex(ValueError, "runtime_stopping"):
            host.operation(self.request("task", id="later", kind="work", payload={}))
        host.step()
        self.assertEqual(
            host.db.execute("SELECT status FROM operations WHERE id='task'").fetchone()[
                0
            ],
            "held",
        )
        host.operation(task)
        host.step()
        self.assertEqual(
            host.db.execute("SELECT status FROM runtimes").fetchone()[0], "stopped"
        )
        self.assertFalse(host.step())

    def test_a_stale_queued_start_cannot_clear_the_stop_barrier(self) -> None:
        host = self.host()
        self.start(host)
        host.operation(self.stop())
        with host.db:
            host.db.execute("UPDATE operations SET status='queued' WHERE id='start'")
        host.step()
        self.assertEqual(
            host.db.execute("SELECT status FROM runtimes").fetchone()[0], "stopping"
        )
        self.assertEqual(
            host.db.execute(
                "SELECT status FROM operations WHERE id='start'"
            ).fetchone()[0],
            "held",
        )
        self.assertEqual(len(self.factories), 1)
        host.step()
        self.assertEqual(
            host.db.execute("SELECT status FROM runtimes").fetchone()[0], "stopped"
        )

    def test_ephemeral_stop_barrier_and_confirmed_closure_both_survive_reopen(
        self,
    ) -> None:
        for completed in (False, True):
            with self.subTest(completed=completed):
                root = self.root / str(completed)
                host = self.host(root)
                self.start(host, journal=False)
                self.factories[-1].durable = False
                host.operation(self.stop())
                if completed:
                    host.step()
                host.close()
                host = self.host(
                    root,
                    factory=lambda *_: self.fail(
                        "Ephemeral stop cannot construct a participant"
                    ),
                )
                self.assertEqual(
                    host.db.execute("SELECT status FROM runtimes").fetchone()[0],
                    "stopped" if completed else "stopping",
                )
                self.assertFalse(host.step())

    def test_actual_process_crashes_at_each_stop_boundary_never_restart_a_participant(
        self,
    ) -> None:
        script = """
import json, os, sys
from pathlib import Path
from remote_agent.band.host import ProgramHost
root, request, point = Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
class Participant:
    durable = True
    def summary(self): return {}
    def control(self, actor, body):
        if point == 'control': os._exit(73)
        return {'action': 'stop', 'status': 'stopped'}
    def close(self):
        if point == 'close': os._exit(73)
host = ProgramHost(root, assessor=lambda *_: {'classification': 'program', 'deployable': True},
                   factory=lambda *_: Participant(), authors={'author'})
host.operation(dict(request, operation='assess', request={'id': 'assessment', 'role': 'worker', 'author_id': 'author'})); host.step()
host.operation(dict(request, operation='start', request={'id': 'start', 'assessment_id': 'assessment', 'mode': 'program', 'journal': True})); host.step()
host.operation(request)
if point == 'queued': os._exit(73)
def trace(sql):
    if point == 'stopped_commit' and sql.startswith("UPDATE runtimes SET status='stopped'"): os._exit(73)
    if point == 'result_commit' and sql.startswith("UPDATE operations SET status='complete'"): os._exit(73)
host.db.set_trace_callback(trace)
host.step()
os._exit(74)
"""
        for point in ("queued", "control", "close", "stopped_commit", "result_commit"):
            with self.subTest(point=point):
                root = self.root / point
                child = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-W",
                        "error",
                        "-c",
                        script,
                        str(root),
                        json.dumps(self.stop()),
                        point,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(child.returncode, 73, child.stderr)
                host = self.host(
                    root,
                    factory=lambda *_: self.fail(
                        "Interrupted stop cannot invoke the factory"
                    ),
                )
                expected = "stopped" if point == "result_commit" else "stopping"
                self.assertEqual(
                    host.db.execute("SELECT status FROM runtimes").fetchone()[0],
                    expected,
                )
                before = host.operation(self.request("status"))
                self.assertEqual(before["control"]["status"], "held")
                self.assertFalse(host.step())
                self.assertEqual(host.operation(self.stop()), before)
                self.assertFalse(host.step())
                self.assertFalse(host.instances)
