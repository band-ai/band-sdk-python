"""The author uses the skill, grammar and protocol limit its claim delivers."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.band import author_service
from remote_agent.band.authoring import Journal, process_one
from remote_agent.band.studio import StudioAgent

SKILL = "---\nname: room-protocol-author\n---\n# Delivered fixture skill: café.\n"
GRAMMAR = "Delivered fixture grammar: roles list in the header.\n"
REQUEST = {
    "operation": "generate",
    "goal": "Formalize the review",
    "history": [{"id": "m1", "role": "User:owner", "text": "Please review."}],
    "source": None,
}
PROTOCOL = {
    "source": "typed fixture source",
    "derivation": [{"evidence": "m1", "rule": "Review"}],
    "unresolved": [],
}
CONFIG = {"model_config": "/unused/model.json", "model_timeout_seconds": 5}


def document(content: str, **fields: str) -> dict[str, str]:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return dict(fields, sha256=digest, content=content)


def delivery() -> dict[str, Any]:
    return {
        "skill": document(SKILL, name="room-protocol-author"),
        "grammar": document(GRAMMAR),
        "max_protocols": 4,
    }


@pytest.mark.contract_linux
class TestDeliveredAuthorContext(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = Journal(self.root)
        self.addCleanup(self.journal.close)
        self.sent: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    def author(self, claim: dict[str, Any], result: dict[str, Any]) -> None:
        """Claim once, generate through the real author, then send the completion."""

        def transport(operation: str, body: dict[str, Any]) -> dict[str, Any]:
            if operation == "claim":
                return claim
            self.sent.append(body)
            return {"id": "job", "attempt": 0, "status": "ready"}

        def invoke(root: Any, prompt: str, schema: Any, **options: Any) -> Any:
            self.requests.append({"prompt": prompt, "schema": schema})
            return result

        studio = StudioAgent(self.journal, transport)
        studio.poll()
        model = partial(author_service.generate, self.root, "BUNDLED", config=CONFIG)
        with patch.object(author_service, "invoke", invoke):
            process_one(self.journal, model)
        studio.poll()

    def test_delivered_skill_grammar_and_limit_shape_the_prompt_and_completion(
        self,
    ) -> None:
        job = {"id": "job", "attempt": 0, "request": REQUEST}
        result = {"protocols": [PROTOCOL, dict(PROTOCOL, source="second source")]}
        self.author({"job": job, **delivery()}, result)

        [request] = self.requests
        self.assertIn("DELIVERED AUTHORING SKILL:\n" + SKILL, request["prompt"])
        self.assertIn("EXACT PINNED GRAMMAR:\n" + GRAMMAR, request["prompt"])
        self.assertIn("lists 1 to 4 protocols", request["prompt"])
        self.assertNotIn("BUNDLED", request["prompt"])
        self.assertEqual(request["schema"]["properties"]["protocols"]["maxItems"], 4)
        self.assertEqual(self.sent, [{"attempt": 0, "result": result}])

    def test_a_tampered_delivery_is_held_without_a_model_call(self) -> None:
        tampered = delivery()
        tampered["skill"] = dict(tampered["skill"], content=SKILL + "injected")
        job = {"id": "job", "attempt": 0, "request": REQUEST}
        self.author({"job": job, **tampered}, PROTOCOL)

        self.assertEqual(self.requests, [])
        self.assertEqual(
            self.sent,
            [
                {
                    "attempt": 0,
                    "failure": "Delivered authoring context rejected by local "
                    "journal: skill sha256 mismatch",
                    "held": True,
                }
            ],
        )

    def test_a_claim_without_delivery_keeps_the_bundled_single_result_author(
        self,
    ) -> None:
        job = {"id": "job", "attempt": 0, "request": REQUEST}
        self.author({"job": job}, PROTOCOL)

        [request] = self.requests
        self.assertIn("BUNDLED AUTHORING SKILL:\n", request["prompt"])
        self.assertIn("EXACT PINNED GRAMMAR:\nBUNDLED", request["prompt"])
        self.assertNotIn("protocols", request["schema"]["properties"])
        self.assertEqual(self.sent, [{"attempt": 0, "result": PROTOCOL}])
