"""Prove the portable H7 oracle rejects actor-scoped namespace regressions."""

from __future__ import annotations

import copy
import json
import unittest

from contracts.spec.tools import h7sharednamespace as h7


class TestH7Oracle(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(h7.PATH.read_text())

    def test_valid_portable_fixture(self) -> None:
        h7.check(self.fixture, self.fixture["expected"])

    def test_per_actor_namespace_mutant_is_rejected_even_with_rewritten_expectation(
        self,
    ) -> None:
        mutant = copy.deepcopy(self.fixture)
        accepted = copy.deepcopy(mutant["expected"][2]["decision"])
        accepted.update(actor="A", role="a")
        mutant["expected"][3]["decision"] = accepted
        mutant["assertions"][1] = {
            "response": 3,
            "pointer": "/decision/status",
            "equals": "accepted",
        }
        with self.assertRaisesRegex(AssertionError, "Cross-actor ID reuse"):
            h7.check(mutant, mutant["expected"])

    def test_same_actor_only_trace_cannot_replace_cross_actor_attack(self) -> None:
        mutant = copy.deepcopy(self.fixture)
        mutant["requests"][3]["actor"] = "B"
        with self.assertRaisesRegex(AssertionError, "actor binding changed"):
            h7.check(mutant, mutant["expected"])

    def test_rejected_attack_must_not_mutate_room_state(self) -> None:
        mutant = copy.deepcopy(self.fixture["expected"])
        mutant[3]["state"]["state"]["version"] += 1
        with self.assertRaisesRegex(AssertionError, "changed authoritative state"):
            h7.verify(mutant)


if __name__ == "__main__":
    unittest.main()
