from __future__ import annotations

import json
import subprocess
import sys
import uuid
from typing import Any

import pytest

from remote_agent.network import HTTPError
from remote_agent.store import Store
from tests.contracts.runtime.bandprogramhost import (
    CheckpointInstance,
    DurableInstance,
    EphemeralInstance,
    InterruptedAssessment,
    ProgramHostFixture,
    RetryingTaskInstance,
)


@pytest.mark.contract_linux
class TestProgramHost(ProgramHostFixture):
    def test_an_unknown_ack_retries_the_same_assessment_and_restart_does_not_resample(
        self,
    ) -> None:
        h = self.host()
        identifier = str(uuid.uuid4())
        request = self.request(
            "assess", id=identifier, role="calculator", author_id=self.author
        )
        first = h.operation(request)
        self.assertEqual(first["assessment"]["status"], "queued")
        self.context["state"]["now"] = 99
        self.assertEqual(h.operation(request), first)
        h.step()
        self.assertEqual(len(self.calls), 1)
        result = h.operation(self.request("status"))
        self.assertEqual(result["assessment"]["status"], "ready")
        self.assertEqual(result["assessment"]["classification"], "program")
        h.close()
        h = self.host()
        self.assertEqual(h.operation(request)["assessment"]["status"], "ready")
        self.assertEqual(len(self.calls), 1)
        changed = json.loads(json.dumps(request))
        changed["request"]["role"] = "other"
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            h.operation(changed)

    def test_fresh_session_reuses_qualified_same_source_assessment_without_resampling(
        self,
    ) -> None:
        h = self.host()
        original_session = self.session
        identifier = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=identifier, role="calculator", author_id=self.author
            )
        )
        h.step()
        self.session = str(uuid.uuid4())
        assessment = h.operation(self.request("status"))["assessment"]
        self.assertIsNotNone(assessment)
        self.assertEqual(
            (assessment["id"], assessment["session"]), (identifier, original_session)
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            h.db.execute(
                "SELECT COUNT(*) FROM operations WHERE kind='assess'"
            ).fetchone()[0],
            1,
        )
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=assessment["id"],
                mode="program",
                journal=True,
            )
        )
        self.assertEqual(
            h.db.execute("SELECT status FROM operations WHERE kind='start'").fetchone()[
                0
            ],
            "queued",
        )

    def test_reused_assessment_remains_visible_after_runtime_progress_and_completion(
        self,
    ) -> None:
        h = self.host(factory=lambda *args: DurableInstance())
        identifier = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=identifier, role="calculator", author_id=self.author
            )
        )
        h.step()
        self.session = str(uuid.uuid4())
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=identifier,
                mode="program",
                journal=True,
            )
        )
        h.step()
        self.context["state"]["accepted_count"] = 1
        for status in ("active", "terminal"):
            self.context["state"]["status"] = status
            view = h.operation(self.request("status"))
            self.assertIsNotNone(view["assessment"])
            self.assertEqual(view["assessment"]["id"], identifier)
            self.assertEqual(view["runtime"]["role"], "calculator")
        self.assertEqual(len(self.calls), 1)

    def test_runtime_keeps_its_pinned_assessment_when_another_session_qualifies(
        self,
    ) -> None:
        h = self.host(factory=lambda *args: DurableInstance())
        original = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=original, role="calculator", author_id=self.author
            )
        )
        h.step()
        running_session = str(uuid.uuid4())
        self.session = running_session
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=original,
                mode="program",
                journal=True,
            )
        )
        h.step()
        self.session = str(uuid.uuid4())
        newer = str(uuid.uuid4())
        h.operation(
            self.request("assess", id=newer, role="calculator", author_id=self.author)
        )
        h.step()
        self.session = running_session
        self.assertEqual(
            h.operation(self.request("status"))["assessment"]["id"], original
        )

    def test_session_specific_assessment_takes_precedence_over_reusable_qualification(
        self,
    ) -> None:
        h = self.host()
        original = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=original, role="calculator", author_id=self.author
            )
        )
        h.step()
        self.session = str(uuid.uuid4())
        current = str(uuid.uuid4())
        h.operation(
            self.request("assess", id=current, role="calculator", author_id=self.author)
        )
        assessment = h.operation(self.request("status"))["assessment"]
        self.assertEqual((assessment["id"], assessment["status"]), (current, "queued"))
        self.assertEqual(len(self.calls), 1)

    def test_reusable_assessment_cannot_cross_room_source_or_nonfresh_session(
        self,
    ) -> None:
        h = self.host()
        identifier = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=identifier, role="calculator", author_id=self.author
            )
        )
        h.step()
        self.session = str(uuid.uuid4())
        for change in ("room", "source", "used", "terminal", "missing_role"):
            with self.subTest(change=change):
                request = json.loads(json.dumps(self.request("status")))
                if change == "room":
                    request["room"] = str(uuid.uuid4())
                elif change == "source":
                    request["context"]["source"]["sha256"] = "other-source"
                elif change == "used":
                    request["context"]["state"]["accepted_count"] = 1
                elif change == "terminal":
                    request["context"]["state"]["status"] = "terminal"
                else:
                    request["context"]["config"]["bindings"] = {}
                self.assertIsNone(h.operation(request)["assessment"])
        self.assertEqual(len(self.calls), 1)

    def test_unqualified_assessment_is_not_offered_for_reuse(self) -> None:
        for verdict in (
            {"classification": "llm", "deployable": False},
            {"classification": "program", "deployable": False},
            {"classification": "program", "deployable": "true"},
        ):
            with self.subTest(verdict=verdict):
                h = self.host(
                    root=self.root / str(uuid.uuid4()),
                    assessor=lambda *args, verdict=verdict: verdict,
                )
                identifier = str(uuid.uuid4())
                h.operation(
                    self.request(
                        "assess",
                        id=identifier,
                        role="calculator",
                        author_id=self.author,
                    )
                )
                h.step()
                self.session = str(uuid.uuid4())
                self.assertIsNone(h.operation(self.request("status"))["assessment"])
                h.close()

    def test_a_second_host_cannot_claim_the_same_registry(self) -> None:
        self.host()
        with self.assertRaisesRegex(ValueError, "host_already_running"):
            self.host()

    def test_status_reports_host_granted_assessment_authors_without_starting_work(
        self,
    ) -> None:
        h = self.host()
        other = "00000000-0000-4000-8000-000000000001"
        h.authors.add(other)
        status = h.operation(self.request("status"))
        self.assertEqual(status["assessment_authors"], sorted([self.author, other]))
        self.assertNotIn("Agent:c", status["assessment_authors"])
        self.assertEqual(self.calls, [])
        self.assertEqual(
            h.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0
        )
        h.authors.remove(other)
        self.assertEqual(
            h.operation(self.request("status"))["assessment_authors"], [self.author]
        )

    def test_start_requires_explicit_independent_qualification_before_queuing_a_runtime(
        self,
    ) -> None:
        for eligibility in ({"deployable": False}, {}, {"deployable": "true"}):
            with self.subTest(eligibility=eligibility):
                h = self.host(
                    root=self.root / str(uuid.uuid4()),
                    assessor=lambda *args, eligibility=eligibility: dict(
                        classification="program",
                        source="unqualified proposal",
                        **eligibility,
                    ),
                )
                try:
                    assessment = str(uuid.uuid4())
                    h.operation(
                        self.request(
                            "assess",
                            id=assessment,
                            role="calculator",
                            author_id=self.author,
                        )
                    )
                    h.step()
                    with self.assertRaisesRegex(
                        ValueError, "validated_assessment_required"
                    ):
                        h.operation(
                            self.request(
                                "start",
                                id=str(uuid.uuid4()),
                                assessment_id=assessment,
                                mode="program",
                                journal=True,
                                target_bytes=100_000_000,
                                minimum_steps=100,
                            )
                        )
                    self.assertEqual(
                        h.db.execute(
                            "SELECT COUNT(*) FROM operations WHERE kind='start'"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        h.db.execute("SELECT COUNT(*) FROM runtimes").fetchone()[0], 0
                    )
                finally:
                    h.close()

    def test_interrupted_assessment_is_held_and_never_automatically_retried(
        self,
    ) -> None:
        def crash(*args: Any) -> None:
            raise InterruptedAssessment()

        h = self.host(assessor=crash)
        identifier = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=identifier, role="calculator", author_id=self.author
            )
        )
        with self.assertRaises(InterruptedAssessment):
            h.step()
        h.close()
        h = self.host()
        self.assertEqual(
            h.operation(self.request("status"))["assessment"]["status"], "held"
        )
        self.assertFalse(h.step())
        self.assertEqual(self.calls, [])

    def test_unknown_author_and_caller_host_permissions_fail_before_work(self) -> None:
        h = self.host()
        with self.assertRaisesRegex(ValueError, "author_unavailable"):
            h.operation(
                self.request(
                    "assess",
                    id=str(uuid.uuid4()),
                    role="calculator",
                    author_id=str(uuid.uuid4()),
                )
            )
        with self.assertRaises(HTTPError):
            h.dispatch(
                "POST",
                "/v1/operations",
                {"actor": "other", "permissions": ["controls"]},
                self.request("status"),
            )

    def test_ephemeral_controls_never_enter_the_durable_operation_registry(
        self,
    ) -> None:
        h = self.host(factory=lambda *args: EphemeralInstance())
        assessment = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=assessment,
                mode="program",
                journal=False,
                target_bytes=100_000_000,
                minimum_steps=100,
            )
        )
        h.step()
        control = self.request(
            "control",
            id=str(uuid.uuid4()),
            action="takeover",
            reason="ephemeral-only-private-reason",
        )
        h.operation(control)
        self.assertEqual(
            h.db.execute(
                "SELECT COUNT(*) FROM operations WHERE kind='control'"
            ).fetchone()[0],
            0,
        )
        h.step()
        self.assertEqual(
            h.operation(self.request("status"))["control"]["status"], "completed"
        )
        self.assertEqual(h.operation(control)["control"]["status"], "completed")
        for path in self.root.glob("registry.sqlite3*"):
            self.assertNotIn(b"ephemeral-only-private-reason", path.read_bytes())

    def test_task_retry_reconciles_the_same_effect_identity_and_ephemeral_inputs_stay_in_memory(
        self,
    ) -> None:
        calls = []
        h = self.host(factory=lambda *args: RetryingTaskInstance(calls))
        assessment = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=assessment,
                mode="program",
                journal=False,
                target_bytes=100_000_000,
                minimum_steps=100,
            )
        )
        h.step()
        task = self.request(
            "task",
            id=str(uuid.uuid4()),
            kind="question",
            payload={"op": "add", "a": 37, "b": 61},
        )
        h.operation(task)
        h.step()
        held = h.operation(self.request("status"))["task"]
        self.assertEqual(held["status"], "held")
        self.assertEqual(held["request"], task["request"])
        self.assertNotIn("context", held)
        self.assertNotIn("actor", held["request"])
        h.operation(task)
        h.step()
        self.assertEqual(
            h.operation(self.request("status"))["task"]["status"], "complete"
        )
        h.operation(task)
        self.assertFalse(h.step())
        self.assertEqual(calls, [task["request"], task["request"]])
        self.assertEqual(
            h.db.execute(
                "SELECT COUNT(*) FROM operations WHERE kind='task'"
            ).fetchone()[0],
            0,
        )

    def test_no_journal_runtime_is_marked_lost_instead_of_restarted_after_host_loss(
        self,
    ) -> None:
        h = self.host()
        identifier = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=identifier, role="calculator", author_id=self.author
            )
        )
        h.step()
        h.operation(
            self.request(
                "start",
                id=str(uuid.uuid4()),
                assessment_id=identifier,
                mode="program",
                journal=False,
                target_bytes=100_000_000,
                minimum_steps=100,
            )
        )
        h.step()
        h.close()
        h = self.host()
        status = h.operation(self.request("status"))
        self.assertEqual(status["runtime"]["status"], "lost")
        self.assertIn("ephemeral", status["runtime"]["hold"])
        self.assertFalse(
            (self.root / "runtimes" / self.session / "agent.sqlite3").exists()
        )

    def test_exact_retry_recovers_a_failed_durable_start_without_replacing_its_configuration(
        self,
    ) -> None:
        calls = []

        def factory(value: Any, assessment: Any, workspace: Any) -> Any:
            calls.append(json.loads(json.dumps(value)))
            store = Store(workspace / "participant.sqlite3")
            if len(calls) == 1:
                store.commit("checkpoint", {}, updates={"checkpoint": {"completed": 3}})
                store.close()
                raise OSError("temporary authority connection failure")
            return CheckpointInstance(store)

        h = self.host(factory=factory)
        assessment = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        identifier = str(uuid.uuid4())
        original = self.request(
            "start",
            id=identifier,
            assessment_id=assessment,
            mode="program",
            journal=True,
            target_bytes=100_000_000,
            minimum_steps=100,
        )
        h.operation(original)
        h.step()
        self.assertEqual(
            h.operation(self.request("status"))["runtime"]["status"], "held"
        )
        self.assertFalse(h.step(), "failed startup must wait for an explicit retry")
        pinned = h.db.execute("SELECT configuration FROM runtimes").fetchone()[0]
        self.context["state"]["accepted_count"] = 8
        self.context["source"]["sha256"] = "later-source"
        h.operation(original)
        h.operation(original)
        self.assertTrue(
            h.step(), "an exact explicit retry must schedule durable recovery"
        )
        status = h.operation(self.request("status"))["runtime"]
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["checkpoint"], {"completed": 3})
        self.assertEqual(calls, [calls[0], calls[0]])
        self.assertEqual(
            h.db.execute("SELECT configuration FROM runtimes").fetchone()[0], pinned
        )
        self.assertEqual(
            h.db.execute(
                "SELECT status FROM operations WHERE id=?", (identifier,)
            ).fetchone()[0],
            "complete",
        )
        h.operation(original)
        self.assertFalse(h.step())
        self.assertEqual(len(calls), 2)

    def test_restart_reconciles_the_exact_queued_start_with_its_recovered_live_instance(
        self,
    ) -> None:
        calls = []

        def factory(value: Any, assessment: Any, workspace: Any) -> Any:
            calls.append(json.loads(json.dumps(value)))
            store = Store(workspace / "participant.sqlite3")
            if len(calls) == 1:
                store.commit("checkpoint", {}, updates={"checkpoint": {"completed": 3}})
                store.close()
                raise OSError("temporary startup failure")
            return CheckpointInstance(store)

        h = self.host(factory=factory)
        assessment, identifier = str(uuid.uuid4()), str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        original = self.request(
            "start",
            id=identifier,
            assessment_id=assessment,
            mode="program",
            journal=True,
        )
        h.operation(original)
        h.step()
        h.operation(original)
        self.assertEqual(
            h.db.execute(
                "SELECT status FROM operations WHERE id=?", (identifier,)
            ).fetchone()[0],
            "queued",
        )
        h.close()
        h = self.host(factory=factory)
        self.assertTrue(h.step())
        self.assertFalse(
            h.step(), "recovery must reconcile the already queued exact start"
        )
        self.assertEqual(
            h.db.execute("SELECT status,hold FROM runtimes").fetchone(),
            ("running", None),
        )
        self.assertEqual(
            h.db.execute(
                "SELECT status FROM operations WHERE id=?", (identifier,)
            ).fetchone()[0],
            "complete",
        )
        self.assertEqual(len(h.instances), 1)
        self.assertEqual(calls, [calls[0], calls[0]])
        self.assertEqual(
            h.operation(self.request("status"))["runtime"]["checkpoint"],
            {"completed": 3},
        )
        changed = json.loads(json.dumps(original))
        changed["request"]["mode"] = "hybrid"
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            h.operation(changed)
        replacement = json.loads(json.dumps(original))
        replacement["request"]["id"] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "runtime_already_exists"):
            h.operation(replacement)
        h.operation(self.request("control", id=str(uuid.uuid4()), action="stop"))
        h.step()
        self.assertFalse(h.instances)
        self.assertEqual(
            h.db.execute("SELECT status FROM runtimes").fetchone()[0], "stopped"
        )

    def test_completed_durable_start_can_retry_a_failed_recovery_without_replacing_its_checkpoint(
        self,
    ) -> None:
        calls = []

        def factory(value: Any, assessment: Any, workspace: Any) -> Any:
            calls.append(json.loads(json.dumps(value)))
            store = Store(workspace / "participant.sqlite3")
            if len(calls) == 1:
                store.commit("checkpoint", {}, updates={"checkpoint": {"completed": 7}})
            if len(calls) == 2:
                store.close()
                raise OSError("temporary recovery connection failure")
            return CheckpointInstance(store)

        h = self.host(factory=factory)
        assessment, identifier = str(uuid.uuid4()), str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        original = self.request(
            "start",
            id=identifier,
            assessment_id=assessment,
            mode="program",
            journal=True,
        )
        h.operation(original)
        h.step()
        h.operation(original)
        self.assertFalse(h.step(), "running instances must not be retried")
        pinned = h.db.execute("SELECT configuration FROM runtimes").fetchone()[0]
        h.close()
        h = self.host(factory=factory)
        h.step()
        self.assertEqual(
            h.db.execute(
                "SELECT status FROM operations WHERE id=?", (identifier,)
            ).fetchone()[0],
            "complete",
        )
        self.assertEqual(
            h.db.execute("SELECT status FROM runtimes").fetchone()[0], "held"
        )
        self.assertFalse(h.instances)
        self.assertFalse(h.step(), "a failed recovery must wait for explicit retry")
        changed = json.loads(json.dumps(original))
        changed["request"]["mode"] = "hybrid"
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            h.operation(changed)
        replacement = json.loads(json.dumps(original))
        replacement["request"]["id"] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "runtime_already_exists"):
            h.operation(replacement)
        self.context["state"]["accepted_count"] = 8
        self.context["source"]["sha256"] = "new-context-must-not-replace-the-pin"
        h.operation(original)
        h.operation(original)
        self.assertTrue(
            h.step(), "an exact start retry must recover the held durable runtime"
        )
        self.assertEqual(
            h.db.execute("SELECT status,hold FROM runtimes").fetchone(),
            ("running", None),
        )
        self.assertEqual(
            h.db.execute("SELECT configuration FROM runtimes").fetchone()[0], pinned
        )
        self.assertEqual(
            h.operation(self.request("status"))["runtime"]["checkpoint"],
            {"completed": 7},
        )
        self.assertEqual(calls, [calls[0]] * 3)
        h.operation(original)
        self.assertFalse(h.step(), "a live recovered instance must not be retried")
        h.operation(self.request("control", id=str(uuid.uuid4()), action="stop"))
        h.step()
        h.operation(original)
        self.assertFalse(h.step(), "stopped runtimes must remain stopped")
        self.assertEqual(
            h.db.execute("SELECT status FROM runtimes").fetchone()[0], "stopped"
        )
        self.assertEqual(len(calls), 3)

    def test_successful_recovery_reconciles_start_interrupted_before_or_after_factory_completion(
        self,
    ) -> None:
        def factory(value: Any, assessment: Any, workspace: Any) -> Any:
            return CheckpointInstance(Store(workspace / "participant.sqlite3"))

        script = """
import json, os, sys
from pathlib import Path
from remote_agent.band.host import ProgramHost
from remote_agent.store import Store
root, request, author, point = Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
def factory(value, assessment, workspace):
    store = Store(workspace / 'participant.sqlite3')
    store.commit('checkpoint', {}, updates={'checkpoint': {'completed': 7}})
    if point == 'factory': os._exit(73)
    return store
h = ProgramHost(root, assessor=lambda *_: {'classification': 'program', 'deployable': True}, factory=factory, authors={author})
assessment = dict(request, operation='assess', request={'id': request['request']['assessment_id'], 'role': 'calculator', 'author_id': author})
h.operation(assessment); h.step()
def trace(sql):
    if point == 'result_commit' and sql.startswith("UPDATE operations SET status='complete'"): os._exit(73)
h.db.set_trace_callback(trace)
h.operation(request); h.step()
os._exit(74)
"""
        for point in ("factory", "result_commit"):
            with self.subTest(point=point):
                root = self.root / point
                identifier = str(uuid.uuid4())
                original = self.request(
                    "start",
                    id=identifier,
                    assessment_id=str(uuid.uuid4()),
                    mode="program",
                    journal=True,
                )
                child = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        script,
                        str(root),
                        json.dumps(original),
                        self.author,
                        point,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(child.returncode, 73, child.stderr)
                h = self.host(root=root, factory=factory)
                self.assertEqual(
                    h.db.execute(
                        "SELECT status FROM operations WHERE id=?", (identifier,)
                    ).fetchone()[0],
                    "held",
                )
                self.assertTrue(h.step())
                self.assertEqual(
                    h.db.execute("SELECT status,hold FROM runtimes").fetchone(),
                    ("running", None),
                )
                status, result = h.db.execute(
                    "SELECT status,result FROM operations WHERE id=?", (identifier,)
                ).fetchone()
                self.assertEqual(
                    status,
                    "complete",
                    "successful recovery must reconcile the interrupted start record",
                )
                self.assertEqual(json.loads(result), {"runtime_id": identifier})
                self.assertEqual(
                    h.operation(self.request("status"))["runtime"]["checkpoint"],
                    {"completed": 7},
                )
                h.operation(original)
                self.assertFalse(h.step())
                self.assertEqual(len(h.instances), 1)

    def test_held_start_retry_preserves_identity_and_does_not_restart_ephemeral_or_live_instances(
        self,
    ) -> None:
        calls = []

        def fail(*args: Any) -> None:
            calls.append(args)
            raise OSError("outcome unknown")

        h = self.host(factory=fail)
        assessment = str(uuid.uuid4())
        h.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        h.step()
        identifier = str(uuid.uuid4())
        original = self.request(
            "start",
            id=identifier,
            assessment_id=assessment,
            mode="program",
            journal=False,
            target_bytes=100_000_000,
            minimum_steps=100,
        )
        h.operation(original)
        h.step()
        changed = json.loads(json.dumps(original))
        changed["request"]["mode"] = "llm"
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            h.operation(changed)
        h.operation(original)
        self.assertFalse(h.step())
        self.assertEqual(len(calls), 1)
        live_calls = []

        def live_factory(*args: Any) -> Any:
            live_calls.append(args)
            return DurableInstance()

        live = self.host(root=self.root / str(uuid.uuid4()), factory=live_factory)
        live.operation(
            self.request(
                "assess", id=assessment, role="calculator", author_id=self.author
            )
        )
        live.step()
        durable = json.loads(json.dumps(original))
        durable["request"]["journal"] = True
        live.operation(durable)
        live.step()
        # The factory returned, but committing its operation result failed.
        with live.db:
            live.db.execute(
                "UPDATE operations SET status='held' WHERE id=?", (identifier,)
            )
            live.db.execute("UPDATE runtimes SET status='held'")
        live.operation(durable)
        self.assertFalse(live.step())
        self.assertEqual(len(live_calls), 1)
