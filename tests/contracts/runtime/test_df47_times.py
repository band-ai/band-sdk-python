from __future__ import annotations

import json

import jsonschema
import pytest

from tests.contracts.runtime.df47times import AcceptedTimesFixture
from tests.paths import CONTRACTS_ROOT


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestAcceptedTimes(AcceptedTimesFixture):
    def test_accepted_at_survives_restart_and_matches_visible_fact_not_read_time(
        self,
    ) -> None:
        message = {
            "id": "q1",
            "session": "s",
            "kind": "question",
            "payload": {"text": "private input"},
            "expected_version": 0,
        }
        original = self.room.publish(message, "user")
        records = self.store.records()
        self.now += 1234
        page = self.inbox("agent")
        self.assertEqual(page["events"][0]["at"], 1_790_000_000_000)
        visible = self.room.view("agent")["view"]["facts"]["q1"]
        self.assertEqual(visible["at"], page["events"][0]["at"])
        self.assertEqual(
            self.store.records(), records, "Reading must not rewrite receipts"
        )
        self.close()
        self.start()
        self.assertEqual(self.inbox("agent"), page)
        self.assertEqual(
            self.room.publish(message, "user"),
            {"status": "duplicate", "original": original},
        )
        schema = json.loads(
            (CONTRACTS_ROOT / "spec/schemas/protocol-v2.schema.json").read_text()
        )
        jsonschema.Draft202012Validator(
            {**schema, "$ref": "#/$defs/standalone_inbox_response"}
        ).validate(page)

    def test_time_metadata_does_not_widen_private_fact_or_inbox_visibility(
        self,
    ) -> None:
        self.room.publish(
            {
                "id": "secret",
                "session": "s",
                "kind": "question",
                "payload": {"text": "secret payload"},
                "expected_version": 0,
            },
            "user",
        )
        projection = self.room.view("observer")
        self.assertEqual(projection["view"]["facts"], {})
        self.assertEqual(self.inbox("observer")["events"], [])
        self.assertEqual(
            projection["checkpoint"]["state"]["facts"]["secret"], {"kind": "question"}
        )
        self.assertNotIn("secret payload", json.dumps(projection))
