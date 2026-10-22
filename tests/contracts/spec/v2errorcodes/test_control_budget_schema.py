"""ControlBudgetSchema specification regression checks."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

import jsonschema

from contracts.spec.tools.buildv2schemas import root as v2
from tests.contracts.spec.v2errorcodes.kit import validator


class TestControlBudgetSchema(unittest.TestCase):
    config: ClassVar[dict[str, Any]] = {
        "session": "s",
        "bindings": {"requester": "r", "assistant": "a"},
        "now": 0,
        "max_messages": 2,
    }

    def valid(self, **changes: Any) -> bool:
        return validator(v2, "config").is_valid({**self.config, **changes})

    def initialized(self, extensions: list[str], **changes: Any) -> bool:
        # Only the budget rule of `initialization`; checkv2 validates whole compiled contracts.
        rule = jsonschema.Draft202012Validator(
            {"$defs": v2["$defs"], "allOf": v2["$defs"]["initialization"]["allOf"]}
        )
        return rule.is_valid(
            {
                "contract": {"card": {"extensions": extensions}},
                "config": {**self.config, **changes},
            }
        )

    def test_config_alone_keeps_the_zero_floor(self) -> None:
        # The reducer keys the budget on declared extensions, which a config cannot see.
        self.assertTrue(self.valid())
        self.assertTrue(self.valid(capabilities=["host.presence/1"], max_controls=0))
        self.assertTrue(
            self.valid(capabilities=["room.contributions/1"], max_controls=1)
        )
        self.assertFalse(self.valid(max_controls=1025))

    def test_declared_signal_extension_needs_two_to_1024_controls(self) -> None:
        for extension in ("host.presence/1", "host.membership/1"):
            with self.subTest(extension=extension):
                capabilities = [extension]
                self.assertFalse(
                    self.initialized([extension], capabilities=capabilities)
                )
                self.assertFalse(
                    self.initialized(
                        [extension], capabilities=capabilities, max_controls=0
                    )
                )
                self.assertFalse(
                    self.initialized(
                        [extension], capabilities=capabilities, max_controls=1
                    )
                )
                self.assertTrue(
                    self.initialized(
                        [extension], capabilities=capabilities, max_controls=2
                    )
                )
                self.assertTrue(
                    self.initialized(
                        [extension], capabilities=capabilities, max_controls=1024
                    )
                )

    def test_capability_without_declaration_needs_no_budget(self) -> None:
        self.assertTrue(
            self.initialized([], capabilities=["host.presence/1"], max_controls=0)
        )
        self.assertTrue(
            self.initialized(["band.permissions/1"], capabilities=["host.membership/1"])
        )
