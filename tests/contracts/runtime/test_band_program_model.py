from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from remote_agent.band.configuration import model_arguments, module_command
from tests.contracts.runtime.bandprogrammodel import ProgramModelFixture


@pytest.mark.contract_linux
class TestProgramModel(ProgramModelFixture):
    def test_cli_reaches_configured_provider_with_and_without_journaling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = root / "provider.py"
            provider.write_text(
                "import json, sys\n"
                "request = json.load(sys.stdin)\n"
                'assert request["profile"] == "band-contract/structured-model-request/1"\n'
                "print(" + repr(json.dumps(self.decision())) + ")\n"
            )
            config = root / "model.json"
            config.write_text(
                json.dumps(
                    {
                        "profile": "band-contract/structured-model/1",
                        "command": [sys.executable, str(provider)],
                        "credentials": {},
                    }
                )
            )
            config.chmod(0o600)
            for ephemeral in (False, True):
                with self.subTest(ephemeral=ephemeral):
                    command = module_command(
                        "program_model",
                        "--audit",
                        str(root / "audit"),
                        *model_arguments(str(config)),
                    )
                    if ephemeral:
                        command.append("--no-journal")
                    result = subprocess.run(
                        command,
                        input=json.dumps(
                            {
                                "operation": "respond",
                                "system_prompt": "Follow the pinned role.",
                            }
                        ),
                        text=True,
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    decision = json.loads(result.stdout)
                    self.assertEqual(decision["state"]["processed"], 2)
                    self.assertEqual(decision["message"]["kind"], "normalized")

    def test_transcodes_non_arithmetic_state_and_message_through_the_actual_response_schema(
        self,
    ) -> None:
        value, observed = self.invoke(self.decision())
        self.assertEqual(
            value,
            {
                "state": {"processed": 2, "labels": ["Résumé"], "cursor": None},
                "message": {
                    "kind": "normalized",
                    "payload": {
                        "input": "r2",
                        "text": "résumé",
                        "valid": True,
                        "tags": ["text", None],
                    },
                },
                "scope": "turn",
            },
        )
        self.assertTrue(observed["audit_exists"])

    def test_keeps_existing_arithmetic_decisions_compatible_with_the_sdk(self) -> None:
        value, _ = self.invoke(
            self.decision(
                state_json='{"answered":1}',
                message={
                    "kind": "answer",
                    "payload_json": '{"request":"q1","value":98}',
                },
            )
        )
        self.assertEqual(
            value,
            {
                "state": {"answered": 1},
                "message": {
                    "kind": "answer",
                    "payload": {"request": "q1", "value": 98},
                },
                "scope": "turn",
            },
        )

    def test_allows_empty_role_state_and_a_legal_no_reply_decision(self) -> None:
        value, _ = self.invoke(
            self.decision(state_json="{}", message=None, scope="ongoing")
        )
        self.assertEqual(value, {"state": {}, "message": None, "scope": "ongoing"})

    def test_history_request_does_not_become_a_state_change(self) -> None:
        query = {
            "after": 0,
            "through": None,
            "limit": 2,
            "kind": "turn",
            "query": "record r1",
        }
        value, _ = self.invoke(
            self.decision(state_json=None, message=None, history_query=query)
        )
        self.assertEqual(
            value,
            {
                "history_query": {
                    "after": 0,
                    "limit": 2,
                    "kind": "turn",
                    "query": "record r1",
                }
            },
        )

    def test_errors_preserve_an_unresolved_turn_instead_of_fabricating_state(
        self,
    ) -> None:
        value, _ = self.invoke(
            self.decision(
                state_json=None, message=None, error="Need the missing input record"
            )
        )
        self.assertEqual(value, {"error": "Need the missing input record"})

    def test_no_journal_model_artifacts_are_ephemeral(self) -> None:
        _, observed = self.invoke(self.decision(), ephemeral=True)
        self.assertTrue(observed["root"].is_relative_to("/dev/shm"))
        self.assertFalse(observed["audit_exists"])
        self.assertFalse(observed["invocation_exists"])

    def test_wake_and_repair_use_role_guidance_without_arithmetic_assumptions(
        self,
    ) -> None:
        for operation, response in [
            (
                "wake",
                {"action": "none", "note": "No intervention needed", "error": None},
            ),
            (
                "repair",
                {
                    "source": 'STATE_VERSION="labels/2"\n',
                    "resume": False,
                    "error": None,
                },
            ),
        ]:
            with self.subTest(operation=operation):
                value, _ = self.invoke(response, operation)
                self.assertEqual(
                    value,
                    {key: item for key, item in response.items() if key != "error"},
                )

    def test_all_operations_can_report_a_trustworthy_inability(self) -> None:
        for operation, response in [
            ("wake", {"action": None, "note": None, "error": "Missing context"}),
            (
                "repair",
                {
                    "source": None,
                    "resume": None,
                    "error": "Cannot preserve accepted behavior",
                },
            ),
        ]:
            with self.subTest(operation=operation):
                value, _ = self.invoke(response, operation)
                self.assertEqual(value, {"error": response["error"]})

    def test_rejects_ambiguous_queries_and_decisions(self) -> None:
        for response in [
            self.decision(error="also an error"),
            self.decision(
                history_query={
                    "after": 0,
                    "through": None,
                    "limit": 1,
                    "kind": "turn",
                    "query": "",
                }
            ),
            self.decision(state_json=None, message=None),
        ]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.invoke(response)

    def test_uses_sdk_json_rules_for_state_and_payload(self) -> None:
        for encoded in [
            "[]",
            "null",
            '{"x":0.5}',
            '{"x":NaN}',
            '{"x":9007199254740992}',
            '{"x":1,"x":2}',
            '{"x":"\\ud800"}',
            '{"x":' + ("[" * 129) + "0" + ("]" * 129) + "}",
        ]:
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                self.invoke(self.decision(state_json=encoded))
        with self.assertRaises(ValueError):
            self.invoke(
                self.decision(message={"kind": "normalized", "payload_json": "[1,2]"})
            )
