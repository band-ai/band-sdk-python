from __future__ import annotations

import http.client
import json
import logging
from typing import Any

import jsonschema
import pytest

from remote_agent.network import HTTPRoom
from remote_agent.transport import EngineProcess, LocalGate
from tests.contracts.runtime.network.kit import AdversarialViewFixture
from tests.paths import CONTRACT_RUNTIME_TESTS, CONTRACTS_ROOT

logger = logging.getLogger(__name__)


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestAdversarialView(AdversarialViewFixture):
    def test_raw_and_hydrated_adversarial_wire_use_the_reply_registry_with_renewal(
        self,
    ) -> None:
        self.four_messages()
        schema = json.loads(
            (CONTRACTS_ROOT / "spec/schemas/protocol-replies.schema.json").read_text()
        )

        def validator(name: Any) -> Any:
            return jsonschema.Draft202012Validator(
                dict(schema, **{"$ref": "#/$defs/" + name})
            )

        for token, grant in self.tokens.items():
            with self.subTest(actor=grant["actor"]):
                code, definition = self.request(
                    "/v1/definition", method="GET", token=token
                )
                self.assertEqual(code, 200)
                self.assertEqual(
                    definition["definition"]["contract"]["renewal_profile"],
                    "band-contract/renewal/1",
                )
                validator("http_definition").validate(definition)
                unknown = json.loads(json.dumps(definition))
                unknown["definition"]["contract"]["card"]["extensions"].append(
                    "unknown.extension/1"
                )
                self.assertFalse(validator("http_definition").is_valid(unknown))
                code, compact = self.request("/v1/view", {}, token=token)
                self.assertEqual(code, 200)
                validator("http_compact_projection").validate(compact)
                self.assertFalse(validator("projection").is_valid(compact))
                hydrated = HTTPRoom(self.server.url, token).view(grant["actor"])
                validator("projection").validate(hydrated)
                self.assertEqual(
                    hydrated["view"]["definition"], definition["definition"]
                )

    def test_role_definitions_are_exact_immutable_and_hide_uninvolved_rules(
        self,
    ) -> None:
        definitions = {}
        records = self.store.records()
        for token, grant in self.tokens.items():
            actor = grant["actor"]
            code, body = self.request("/v1/definition", method="GET", token=token)
            self.assertEqual(code, 200)
            self.assertEqual(
                body["definition"], self.room.view(actor)["view"]["definition"]
            )
            definitions[token] = body
            role = body["definition"]["role"]["name"]
            rules = [
                rule
                for state in body["definition"]["states"].values()
                for rule in state["rules"]
            ]
            rules += body["definition"]["any_rules"] + body["definition"]["room_rules"]
            self.assertTrue(
                all(role in rule["from"] or role in rule["to"] for rule in rules)
            )
        self.assertEqual(self.store.records(), records)
        self.four_messages()
        records = self.store.records()
        for token, expected in definitions.items():
            self.assertEqual(
                self.request("/v1/definition", method="GET", token=token),
                (200, expected),
            )
        self.assertEqual(self.store.records(), records)

    def test_four_accepted_messages_fit_8kb_preserving_facts_duties_and_local_gate(
        self,
    ) -> None:
        self.four_messages()
        sizes = {}
        for token, grant in self.tokens.items():
            actor = grant["actor"]
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.port, timeout=3
            )
            try:
                connection.request(
                    "POST",
                    "/v1/view",
                    body=b"{}",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                encoded = response.read()
                self.assertEqual(response.status, 200)
                content_length = response.getheader("Content-Length")
                assert content_length is not None
                self.assertEqual(int(content_length), len(encoded))
            finally:
                connection.close()
            sizes[actor] = len(encoded)
            compact = json.loads(encoded)
            self.assertLess(len(encoded), 8_192, (actor, len(encoded)))
            expected = self.room.view(actor)
            del expected["view"]["definition"]
            self.assertEqual(compact, expected)
            self.assertEqual(compact["view"]["version"], 4)
            self.assertEqual(compact["view"]["state"], "round2")
            self.assertEqual(len(compact["view"]["facts"]), 0 if actor == "ada" else 4)
            if actor == "victor":
                checker = EngineProcess(
                    CONTRACT_RUNTIME_TESTS / "fixtures/adversarial-plan.band"
                )
                self.addCleanup(checker.close)
                result = LocalGate(checker).check(
                    compact,
                    {
                        "id": "ack2",
                        "session": "df05",
                        "kind": "ack",
                        "expected_version": 4,
                        "payload": {"request": "r2"},
                    },
                    actor,
                    0,
                )
                self.assertEqual(result["status"], "accepted", result)
        logger.debug("Wire bytes after four acceptances: %s", sizes)
