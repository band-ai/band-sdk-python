from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.commands import failure_capture
from remote_agent.controller import AgentError
from remote_agent.invocations import InvocationJournal, InvocationUnresolved
from remote_agent.program import Artifact, Program, ProgramError
from remote_agent.store import Store
from tests.contracts.runtime.hybrid import HybridFixture
from tests.contracts.runtime.invocations.kit import InterruptedHost
from tests.contracts.runtime.program import SOURCE


@pytest.mark.contract_isolation
@pytest.mark.contract_engine
class TestInvocation(HybridFixture):
    def test_repeated_candidate_validation_reuses_exact_probe_records(self) -> None:
        agent = self.start(
            validator=lambda candidate, bundle: {
                "ok": True,
                "checks": ["answer semantics"],
            }
        )
        for _ in range(3):
            self.assertEqual(agent.receive(self.question())["status"], "completed")
        bundle = agent._bundle(view=self.room.view("agent", 0)["view"])
        history = agent._history()
        agent._validate_candidate(agent.program, bundle, history)
        records = self.store.records()
        probes = [
            r["data"]
            for r in records
            if r["kind"] == "program_input" and r["data"]["purpose"] == "validation"
        ]
        self.assertEqual(len(probes), 4)
        with patch.object(
            Program,
            "execute",
            side_effect=AssertionError("identical probe re-executed"),
        ):
            for _ in range(3):
                agent._validate_candidate(agent.program, bundle, history)
        self.assertEqual(self.store.records(), records)
        # New exact inputs must still be validated and retained, not mistaken for
        # a prior candidate-history combination.
        self.assertEqual(agent.receive(self.question())["status"], "completed")
        before = len(self.store.records())
        agent._validate_candidate(agent.program, bundle, agent._history())
        self.assertEqual(len(self.store.records()), before + 2)

    def test_failed_initialization_retries_after_the_host_is_fixed(self) -> None:
        with (
            patch.object(
                Program,
                "execute",
                return_value=failure_capture(RuntimeError("transient sandbox failure")),
            ),
            self.assertRaises(ProgramError),
        ):
            self.start()
        self.assertIsNone(self.store.get("config"))
        self.assertEqual(self.start().status()["mode"], "program")
        inputs = [
            r["data"] for r in self.store.records() if r["kind"] == "program_input"
        ]
        self.assertEqual(len(inputs), 2)
        self.assertNotEqual(inputs[0]["id"], inputs[1]["id"])
        self.assertEqual([r["purpose"] for r in inputs], ["initialize", "initialize"])

    def test_interrupted_initialization_preserves_the_old_attempt_and_retries(
        self,
    ) -> None:
        with (
            patch.object(Program, "execute", side_effect=InterruptedHost),
            self.assertRaises(InterruptedHost),
        ):
            self.start()
        inputs = [
            r["data"] for r in self.store.records() if r["kind"] == "program_input"
        ]
        interrupted = inputs[0]["id"]
        self.assertEqual(self.start().status()["mode"], "program")
        self.assertEqual(
            InvocationJournal(self.store).recorded(interrupted)["status"], "started"
        )
        self.assertEqual(
            len([r for r in self.store.records() if r["kind"] == "initialize"]), 1
        )

    def test_uncommitted_initialization_allows_corrected_configuration(self) -> None:
        with (
            patch.object(Program, "execute", side_effect=InterruptedHost),
            self.assertRaises(InterruptedHost),
        ):
            self.start(model_timeout=1)
        self.assertEqual(self.start(model_timeout=2).status()["mode"], "program")
        self.assertEqual(self.store.get("config")["model_timeout_ms"], 2000)
        self.assertIsNone(self.store.get("pending_configuration"))
        with self.assertRaisesRegex(AgentError, "Pinned host configuration changed"):
            self.start(model_timeout=3)

    def test_exact_program_inputs_and_all_results_are_durable_before_publication(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        observed = []
        publish = self.room.publish

        def inspect_before_send(*args: Any) -> Any:
            records = self.store.records()
            inputs = [r for r in records if r["kind"] == "program_input"]
            outputs = [r for r in records if r["kind"] == "program_output"]
            observed.append((inputs, outputs))
            return publish(*args)

        with patch.object(self.room, "publish", inspect_before_send):
            self.assertEqual(agent.receive(event)["status"], "completed")
        inputs, outputs = observed[0]
        self.assertEqual(len(inputs), 2)  # initial/describe and handle
        self.assertEqual(len(outputs), 2)
        request = inputs[-1]["data"]["request"]
        self.assertEqual(
            request["event"], self.store.get("input:" + event["id"])["event"]
        )
        self.assertEqual(request["event"]["payload"], event["payload"])
        self.assertEqual(request["state"], {"count": 0})
        self.assertEqual(request["artifact"]["sha256"], self.initial.sha256)
        self.assertEqual(request["view"]["version"], 1)
        self.assertEqual(outputs[-1]["data"]["id"], inputs[-1]["data"]["id"])
        self.assertLess(inputs[-1]["sequence"], outputs[-1]["sequence"])
        self.assertTrue(outputs[-1]["data"]["execution"]["complete"])

    def test_completed_callback_is_reused_after_crash_before_outbox_staging(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        commit = self.store.commit

        def crash_before_stage(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "stage":
                raise InterruptedHost()
            return commit(kind, *args, **kwargs)

        with (
            patch.object(self.store, "commit", side_effect=crash_before_stage),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(event)
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 1)
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        # Recovery must not need to execute even the metadata callback again.
        with patch.object(
            Program, "describe", side_effect=AssertionError("re-executed describe")
        ):
            agent = self.start()
        with patch.object(
            Program, "execute", side_effect=AssertionError("re-executed handle")
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)
        self.assertEqual(self.provider.calls, [])

    def test_failed_program_diagnostics_and_error_result_are_retained_before_fallback(
        self,
    ) -> None:
        broken = (
            SOURCE
            + """
def handle(event, state, context):
    import sys
    print("diagnostic-before-failure", file=sys.stderr)
    raise RuntimeError("record-this-failure")
"""
        )
        agent = self.start(broken, max_repairs=0)
        self.assertEqual(agent.receive(self.question())["producer"], "llm")
        outputs = [
            r["data"] for r in self.store.records() if r["kind"] == "program_output"
        ]
        self.assertEqual(len(outputs), 2)
        capture = outputs[-1]["execution"]
        self.assertIn(b"diagnostic-before-failure", base64.b64decode(capture["stderr"]))
        self.assertIn(b"record-this-failure", base64.b64decode(capture["stdout"]))
        self.assertTrue(capture["complete"])

    def test_write_before_execution_includes_the_exact_worker_request(self) -> None:
        original = Program.execute
        seen = []

        def inspect_input(program: Any, request: Any, **kwargs: Any) -> Any:
            inputs = [r for r in self.store.records() if r["kind"] == "program_input"]
            self.assertTrue(inputs)
            self.assertEqual(inputs[-1]["data"]["request"], request)
            seen.append(request["op"])
            return original(program, request, **kwargs)

        with patch.object(Program, "execute", inspect_input):
            agent = self.start()
            self.assertEqual(agent.receive(self.question())["status"], "completed")
        self.assertEqual(seen, ["describe", "handle"])

    def test_database_failure_recording_output_holds_without_reexecuting_or_publishing(
        self,
    ) -> None:
        agent = self.start()
        event = self.question()
        commit = self.store.commit

        def fail_output(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "program_output":
                raise RuntimeError("output commit failed")
            return commit(kind, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=fail_output):
            self.assertEqual(agent.receive(event)["status"], "held")
        with patch.object(
            Program, "execute", side_effect=AssertionError("must not rerun")
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "held")
        self.assertIn("no durable result", result["reason"])
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 1)
        self.assertEqual(self.provider.calls, [])

    def test_whole_process_crash_on_each_side_of_the_result_commit(self) -> None:
        script = """
import json,os,sys
from remote_agent.store import Store
from remote_agent.program import Artifact,Program
from remote_agent.invocations import InvocationJournal
data=json.load(sys.stdin)
store=Store(data["database"])
commit=store.commit
def crash(kind,*args,**kwargs):
    result=commit(kind,*args,**kwargs)
    if kind==data["boundary"]: os._exit(91)
    return result
store.commit=crash
program=Program(Artifact(**data["artifact"]),journal=InvocationJournal(store))
program.handle(data["event"], {"count":0}, {}, invocation_id="crash",purpose="turn")
"""
        event = {"id": "q", "payload": {"text": "ping"}}
        for boundary in ("program_input", "program_output"):
            with self.subTest(boundary=boundary):
                path = self.path / (boundary + ".sqlite3")
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    input=json.dumps(
                        {
                            "database": str(path),
                            "boundary": boundary,
                            "artifact": self.initial.to_dict(),
                            "event": event,
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 91, result.stderr)
                with Store(path) as store:
                    journal = InvocationJournal(store)
                    program = Program(self.initial, journal=journal)
                    with patch.object(
                        program,
                        "execute",
                        side_effect=AssertionError("must not execute"),
                    ):
                        if boundary == "program_input":
                            with self.assertRaises(InvocationUnresolved):
                                program.handle(
                                    event,
                                    {"count": 0},
                                    {},
                                    invocation_id="crash",
                                    purpose="turn",
                                )
                            self.assertEqual(
                                journal.recorded("crash")["status"], "started"
                            )
                        else:
                            output = program.handle(
                                event,
                                {"count": 0},
                                {},
                                invocation_id="crash",
                                purpose="turn",
                            )
                            self.assertEqual(
                                output["message"]["payload"]["text"], "pong"
                            )
                            self.assertEqual(
                                journal.recorded("crash")["status"], "completed"
                            )

    def test_output_overflow_is_an_explicit_incomplete_failure_with_retained_bytes(
        self,
    ) -> None:
        source = SOURCE + "\ndef handle(*args):\n    print('x' * 2000000)\n"
        artifact = Artifact.publish(source, self.path / "artifacts")
        journal = InvocationJournal(self.store)
        program = Program(artifact, journal=journal)
        with self.assertRaisesRegex(ProgramError, "output capacity"):
            program.handle({}, {}, {}, invocation_id="overflow")
        execution = journal.recorded("overflow")["execution"]
        self.assertFalse(execution["complete"])
        self.assertEqual(
            len(base64.b64decode(execution["stdout"]))
            + len(base64.b64decode(execution["stderr"])),
            journal.MAX_BYTES,
        )
