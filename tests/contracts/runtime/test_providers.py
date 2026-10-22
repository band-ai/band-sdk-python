from __future__ import annotations

import json
from pathlib import Path

import pytest

from remote_agent.program import Artifact, Program
from remote_agent.providers import CommandProvider, CommandValidator, ProviderError
from tests.contracts.runtime.commanddeadline import command_deadline
from tests.contracts.runtime.processgate import ProcessGate
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.providers import ProvidersFixture


class TestProviders(ProvidersFixture):
    @pytest.mark.contract_linux
    def test_provider_receives_failure_prompt_and_pinned_source_and_returns_structured_reply(
        self,
    ) -> None:
        argv = self.command("""
import json,sys
r=json.load(sys.stdin)
assert r['operation']=='respond'
assert 'protocol validity' in r['system_prompt'].lower()
assert 'COVERAGE' in r['program_source']
print(json.dumps({'state':{'count':1},'message':{'kind':'answer','payload':{'request':'q','text':'pong'}},'scope':'turn'}))
""")
        provider = CommandProvider(argv, self.path / "artifacts")
        result = provider.respond(self.bundle)
        self.assertEqual(result["message"]["payload"]["text"], "pong")

    @pytest.mark.contract_isolation
    def test_repair_writes_a_new_immutable_artifact_and_runs_host_validator(
        self,
    ) -> None:
        fixed = SOURCE.replace('"text": "pong"', '"text": "updated"')
        argv = self.command(
            "import json\nprint("
            + repr(json.dumps({"source": fixed, "resume": True}))
            + ")\n"
        )
        provider = CommandProvider(argv, self.path / "artifacts")
        result = provider.repair(self.bundle)
        candidate = Program(Artifact(**result["artifact"]))
        self.assertNotEqual(candidate.artifact.sha256, self.artifact.sha256)
        self.assertEqual(Path(self.artifact.path).read_text(), SOURCE)
        self.assertEqual(candidate.describe({})["schema"], "1")
        validator_argv = self.command("""
import json,sys
r=json.load(sys.stdin)
assert r['operation']=='validate'
assert r['candidate']['sha256']
print(json.dumps({'ok':True,'checks':['candidate schema and selected fixture']}))
""")
        validator = CommandValidator(validator_argv)
        self.assertTrue(validator(candidate, self.bundle)["ok"])

    @pytest.mark.contract_linux
    def test_program_source_modification_is_detected_before_sending_it_to_provider(
        self,
    ) -> None:
        argv = self.command("raise RuntimeError('should not run')\n")
        provider = CommandProvider(argv, self.path / "artifacts")
        Path(self.artifact.path).write_text("changed")
        with self.assertRaisesRegex(ProviderError, "hash"):
            provider.respond(self.bundle)

    @pytest.mark.contract_linux
    def test_provider_failure_timeout_and_unbounded_output_are_reported(self) -> None:
        gate = ProcessGate()
        self.addCleanup(gate.close)
        provider = CommandProvider(gate.command(), self.path / "artifacts", timeout=10)
        with (
            command_deadline(gate.await_entry) as deadline,
            self.assertRaisesRegex(ProviderError, "Callback timeout"),
        ):
            provider.wake(self.bundle)
        self.assertTrue(deadline.expired)
        gate.await_closed()
        for source, reason in [
            ("print('x'*2000000)", "capacity"),
            ("raise RuntimeError('failed')", "status"),
        ]:
            with self.subTest(reason=reason):
                provider = CommandProvider(
                    self.command(source), self.path / "artifacts", timeout=10
                )
                with self.assertRaisesRegex(ProviderError, reason):
                    provider.wake(self.bundle)

    @pytest.mark.contract_linux
    def test_repair_must_supply_python_source_and_an_explicit_boolean_resume_policy(
        self,
    ) -> None:
        for response in [
            {"source": SOURCE, "resume": "yes"},
            {"artifact": self.artifact.to_dict()},
            {"error": "Judgment cannot be replaced"},
        ]:
            argv = self.command("print(" + repr(json.dumps(response)) + ")")
            with self.assertRaises(ProviderError):
                CommandProvider(argv, self.path / "artifacts").repair(self.bundle)
