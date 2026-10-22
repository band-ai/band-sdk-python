"""Reply alternatives (contract.replies/1): the protocol/6 schema describes what the engine emits.

fixtures/replies-engine-sample.json holds the reference engine's own output for the deploy
contract of contract-lab/test/support/replies.exs: the compiled object, checkpoints in W,
after a reply and after the due-M timeout, the replier's view, and its role definition.
"""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

import jsonschema

from contracts.spec.tools.buildrepliesschemas import root
from tests.paths import CONTRACT_SPEC_ROOT
from tests.paths import CONTRACTS_ROOT as ROOT

SAMPLE = json.loads(
    (CONTRACT_SPEC_ROOT / "tools/fixtures/replies-engine-sample.json").read_text()
)


def validator(schema: dict[str, Any], name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/" + name})


class TestRepliesSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = root
        cls.previous = json.loads(
            (ROOT / "spec/schemas/protocol-contributions.schema.json").read_text()
        )

    def test_the_committed_schema_is_the_generated_one(self) -> None:
        committed = json.loads(
            (ROOT / "spec/schemas/protocol-replies.schema.json").read_text()
        )
        self.assertEqual(committed, self.schema)
        jsonschema.Draft202012Validator.check_schema(committed)

    def test_engine_output_validates(self) -> None:
        for key, name in [
            ("compiled", "compiled"),
            ("checkpoint_waiting", "checkpoint_data"),
            ("checkpoint_replied", "checkpoint_data"),
            ("checkpoint_expired", "checkpoint_data"),
            ("view", "view"),
            ("role_definition", "role_definition"),
        ]:
            with self.subTest(key=key):
                validator(self.schema, name).validate(SAMPLE[key])

    def test_the_saved_wait_is_required_under_state_6_and_refused_under_older_profiles(
        self,
    ) -> None:
        waiting = SAMPLE["checkpoint_waiting"]
        self.assertEqual(waiting["profile"], "band-contract/state/6")
        missing = copy.deepcopy(waiting)
        del missing["state"]["wait"]
        with self.assertRaises(jsonschema.ValidationError):
            validator(self.schema, "checkpoint_data").validate(missing)
        older = copy.deepcopy(waiting)
        older["profile"] = "band-contract/state/3"
        with self.assertRaises(jsonschema.ValidationError):
            validator(self.schema, "checkpoint_data").validate(older)
        extra = copy.deepcopy(waiting)
        extra["state"]["wait"]["extra"] = 1
        with self.assertRaises(jsonschema.ValidationError):
            validator(self.schema, "checkpoint_data").validate(extra)

    def test_a_reset_of_scope_wait_names_its_duty(self) -> None:
        reset = {"profile": "band-contract/renewal/1", "scope": "wait", "duty": "due-4"}
        validator(self.schema, "reset").validate(reset)
        with self.assertRaises(jsonschema.ValidationError):
            validator(self.schema, "reset").validate({**reset, "duty": "rule-4"})

    def test_literals_are_enum_members_or_booleans_only(self) -> None:
        rule = next(
            r
            for r in SAMPLE["compiled"]["states"]["deploying"]["rules"]
            if r["id"] == "rule-3"
        )
        validator(self.schema, "rule").validate(rule)
        for bad in ({"retryable": 1}, {"retryable": None}, {"Bad": True}):
            with self.subTest(bad=bad), self.assertRaises(jsonschema.ValidationError):
                validator(self.schema, "rule").validate({**rule, "literals": bad})

    def test_every_exit_names_its_sender_roles(self) -> None:
        # K-2: an exit from W is any state-moving rule that applies there, with who sends it.
        exit_ = {
            "rule": "rule-10",
            "kind": "cancel",
            "target": "failed",
            "from": ["lead"],
        }
        validator(self.schema, "wait_exit").validate(exit_)
        bare = {key: value for key, value in exit_.items() if key != "from"}
        with self.assertRaises(jsonschema.ValidationError):
            validator(self.schema, "wait_exit").validate(bare)
        definition = SAMPLE["role_definition"]["states"]["deploying"]["wait"]
        self.assertTrue(
            all("from" in e for e in definition["exits"]), definition["exits"]
        )

    def test_the_previous_schema_refuses_reply_structures_fail_closed(self) -> None:
        # S6: a reader on protocol/5 rejects the compiled object and the state/6 checkpoint.
        for key, name in [
            ("compiled", "compiled"),
            ("checkpoint_waiting", "checkpoint_data"),
        ]:
            with self.subTest(key=key), self.assertRaises(jsonschema.ValidationError):
                validator(self.previous, name).validate(SAMPLE[key])


if __name__ == "__main__":
    unittest.main()
