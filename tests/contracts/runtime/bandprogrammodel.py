from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jsonschema import Draft202012Validator

from remote_agent.band import program_model


class ProgramModelFixture(unittest.TestCase):
    def invoke(
        self, response: Any, operation: str = "respond", *, ephemeral: bool = False
    ) -> tuple[Any, ...]:
        observed: dict[str, Any] = {}
        request = {
            "operation": operation,
            "system_prompt": "Follow the pinned documented role and program state.",
            "program_source": 'STATE_VERSION="labels/2"\n',
            "bundle": {"state": {"processed": 1, "labels": []}, "role": "normalizer"},
        }
        original = copy.deepcopy(response)
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit"

            def complete(root: Any, prompt: Any, schema: Any, **options: Any) -> Any:
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(response)

                def closed(value: Any) -> None:
                    if isinstance(value, dict):
                        if value.get("type") == "object":
                            self.assertIs(value["additionalProperties"], False)
                            self.assertEqual(
                                set(value["required"]), set(value["properties"])
                            )
                        for child in value.values():
                            closed(child)
                    elif isinstance(value, list):
                        for child in value:
                            closed(child)

                closed(schema)
                self.assertNotIn("Use the exact small state", prompt)
                self.assertNotIn("A successful arithmetic answer increments", prompt)
                observed.update(root=Path(root), prompt=prompt)
                Path(root).mkdir(parents=True, exist_ok=True)
                (Path(root) / "invocation.txt").write_text("scripted provider evidence")
                return response

            config = Path(directory) / "model.json"
            config.write_text("{}")
            config.chmod(0o600)
            argv = [
                "program_model.py",
                "--audit",
                str(audit),
                "--model-config",
                str(config),
                "--model-config-sha256",
                hashlib.sha256(config.read_bytes()).hexdigest(),
            ] + (["--no-journal"] if ephemeral else [])
            output = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch.object(sys, "stdin", io.StringIO(json.dumps(request))),
                patch.object(sys, "stdout", output),
                patch("remote_agent.band.program_model.invoke", side_effect=complete),
            ):
                program_model.main()
            observed["audit_exists"] = audit.exists()
            observed["invocation_exists"] = observed["root"].exists()
            self.assertEqual(
                response,
                original,
                "Transcoding must not mutate the retained provider result",
            )
            return json.loads(output.getvalue()), observed

    def decision(self, **overrides: Any) -> dict[str, Any]:
        value = {
            "state_json": json.dumps(
                {"processed": 2, "labels": ["Résumé"], "cursor": None}
            ),
            "message": {
                "kind": "normalized",
                "payload_json": json.dumps(
                    {
                        "input": "r2",
                        "text": "résumé",
                        "valid": True,
                        "tags": ["text", None],
                    }
                ),
            },
            "scope": "turn",
            "history_query": None,
            "error": None,
        }
        return dict(value, **overrides)
