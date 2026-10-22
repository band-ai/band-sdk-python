from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Any, Never
from unittest.mock import patch

import pytest

from remote_agent.modeljournal import ModelJournal, ModelUnresolved
from remote_agent.program import Artifact
from remote_agent.providers import CommandProvider
from remote_agent.storage import canonical
from remote_agent.store import Store
from tests.contracts.runtime.modeljournal import InterruptedHost, ModelJournalFixture
from tests.contracts.runtime.program import SOURCE


class TestModelJournal(ModelJournalFixture):
    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_model_request_and_result_are_durable_before_effect_publication(
        self,
    ) -> None:
        agent = self.start()
        event = self.question("Explain the tradeoff")
        publish = self.room.publish
        observed = []

        def before_publish(*args: Any) -> Any:
            observed.extend(
                [
                    r
                    for r in self.store.records()
                    if r["kind"] in ("model_input", "model_output")
                ]
            )
            return publish(*args)

        with patch.object(self.room, "publish", before_publish):
            self.assertEqual(agent.receive(event)["status"], "completed")
        self.assertEqual([r["kind"] for r in observed], ["model_input", "model_output"])
        request, output = [r["data"] for r in observed]
        self.assertEqual(request["request"]["bundle"]["input"]["id"], event["id"])
        self.assertEqual(request["request"]["bundle"], self.provider.bundles[0])
        self.assertEqual(
            output["execution"]["value"]["message"]["payload"]["text"],
            "A considered answer.",
        )

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_crash_after_completed_model_call_reuses_answer_without_resampling(
        self,
    ) -> None:
        agent = self.start()
        commit = self.store.commit

        def crash_before_stage(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "stage":
                raise InterruptedHost()
            return commit(kind, *args, **kwargs)

        with (
            patch.object(self.store, "commit", side_effect=crash_before_stage),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(self.question("Needs language judgment"))
        self.assertEqual(self.provider.calls, ["respond"])
        agent = self.restart()
        with patch.object(
            self.provider, "respond", side_effect=AssertionError("resampled model")
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.get("model_calls"), 1)
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 2)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_crash_after_model_input_commit_holds_without_invoking_provider(
        self,
    ) -> None:
        agent = self.start()
        commit = self.store.commit

        def crash_after_input(kind: Any, *args: Any, **kwargs: Any) -> Any:
            result = commit(kind, *args, **kwargs)
            if kind == "model_input":
                raise InterruptedHost()
            return result

        with (
            patch.object(self.store, "commit", side_effect=crash_after_input),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(self.question("Needs language judgment"))
        agent = self.restart()
        with patch.object(
            self.provider,
            "respond",
            side_effect=AssertionError("invoked uncertain call"),
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "held")
        self.assertIn("no durable result", result["reason"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_failed_output_commit_holds_instead_of_publishing_or_resampling(
        self,
    ) -> None:
        agent = self.start()
        commit = self.store.commit

        def fail_output(kind: Any, *args: Any, **kwargs: Any) -> Any:
            if kind == "model_output":
                raise RuntimeError("output commit failed")
            return commit(kind, *args, **kwargs)

        with patch.object(self.store, "commit", side_effect=fail_output):
            self.assertEqual(
                agent.receive(self.question("Needs judgment"))["status"], "held"
            )
        with patch.object(
            self.provider, "respond", side_effect=AssertionError("resampled model")
        ):
            self.assertEqual(agent.recover()["status"], "held")
        self.assertEqual(self.provider.calls, ["respond"])
        self.assertEqual(self.room.view("user", 0)["view"]["version"], 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_provider_context_is_bounded_without_recursive_invocation_inputs(
        self,
    ) -> None:
        agent = self.start()
        for number in range(30):
            self.store.commit(
                "model_input",
                {
                    "id": "history-" + str(number),
                    "request": {"bundle": {"text": "x" * 60000}},
                },
            )
        bundle = agent._bundle()
        self.assertLessEqual(len(canonical(bundle)), 131072)
        self.assertEqual(bundle["state"], {"count": 0})
        self.assertTrue(bundle["journal_tail"])
        self.assertNotIn("request", bundle["journal_tail"][-1]["data"])

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_command_provider_records_exact_request_and_raw_stdout_stderr(self) -> None:
        command = self.path / "provider.py"
        command.write_text("""
import json,sys
request=json.load(sys.stdin)
bundle=request['bundle']
print('provider diagnostic',file=sys.stderr)
print(json.dumps({'state':{'count':1},'scope':'turn','message':{'kind':'answer','payload':{
    'request':bundle['input']['id'],'text':'Recorded answer.'}}}))
""")
        provider = CommandProvider(
            [sys.executable, str(command)], self.path / "artifacts"
        )
        agent = self.start(provider=provider)
        execute = provider.execute
        observed = []

        def inspect(request: Any, **options: Any) -> Any:
            inputs = [r for r in self.store.records() if r["kind"] == "model_input"]
            self.assertEqual(inputs[-1]["data"]["request"], request)
            observed.append(request)
            return execute(request, **options)

        with patch.object(provider, "execute", side_effect=inspect):
            self.assertEqual(
                agent.receive(self.question("Needs judgment"))["status"], "completed"
            )
        self.assertEqual(len(observed), 1)
        self.assertIn("COVERAGE", observed[0]["program_source"])
        output = next(
            r["data"] for r in self.store.records() if r["kind"] == "model_output"
        )
        execution = output["execution"]
        self.assertIn(b"provider diagnostic", base64.b64decode(execution["stderr"]))
        self.assertIn(b"Recorded answer.", base64.b64decode(execution["stdout"]))
        self.assertEqual(execution["exit_code"], 0)

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_whole_process_crashes_preserve_completed_and_unknown_model_calls(
        self,
    ) -> None:
        script = """
import json,os,sys
from remote_agent.store import Store
from remote_agent.modeljournal import ModelJournal
data=json.load(sys.stdin)
store=Store(data['path']); commit=store.commit
def crash(kind,*args,**kwargs):
    result=commit(kind,*args,**kwargs)
    if kind==data['boundary']:os._exit(92)
    return result
store.commit=crash
ModelJournal(store).run('call','respond',{'profile':'test/1'},lambda:{'bundle':{'input':{'id':'q'}}},
    lambda request:{'profile':'remote-agent/callback-result/1','value':{'answer':'recorded'},'failure':None,'uncertain':False},
    max_calls=2,timeout_ms=1000)
"""
        for boundary in ("model_input", "model_output"):
            path = self.path / (boundary + ".db")
            process = subprocess.run(
                [sys.executable, "-c", script],
                input=json.dumps({"path": str(path), "boundary": boundary}),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(process.returncode, 92, process.stderr)
            with Store(path) as store:

                def forbidden(*args: Any) -> Never:
                    raise AssertionError("provider was repeated")

                journal = ModelJournal(store)
                if boundary == "model_input":
                    with self.assertRaises(ModelUnresolved):
                        journal.run(
                            "call",
                            "respond",
                            {"profile": "test/1"},
                            forbidden,
                            forbidden,
                            max_calls=2,
                            timeout_ms=1000,
                        )
                else:
                    result = journal.run(
                        "call",
                        "respond",
                        {"profile": "test/1"},
                        forbidden,
                        forbidden,
                        max_calls=2,
                        timeout_ms=1000,
                    )
                    self.assertEqual(result["value"], {"answer": "recorded"})
                self.assertEqual(store.get("model_calls"), 1)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_recorded_ongoing_delegation_atomically_preserves_its_routing_policy(
        self,
    ) -> None:
        source = SOURCE.replace(
            'Delegate("Requires language judgment")',
            'Delegate("Requires language judgment", scope="ongoing", repair=True)',
        )
        self.initial = Artifact.publish(source, self.path / "artifacts")
        agent = self.start()
        commit = self.store.commit

        def crash_after_delegation(kind: Any, *args: Any, **kwargs: Any) -> Any:
            result = commit(kind, *args, **kwargs)
            if kind == "delegate":
                raise InterruptedHost()
            return result

        with (
            patch.object(self.store, "commit", side_effect=crash_after_delegation),
            self.assertRaises(InterruptedHost),
        ):
            agent.receive(self.question("Needs judgment"))
        self.assertEqual(self.store.get("execution")["producer"], "llm")
        self.assertEqual(self.store.get("mode"), "llm")
        self.assertEqual(self.store.get("repair")["status"], "requested")
