"""ReplyRequiredDoc specification regression checks."""

from __future__ import annotations

import json
import unittest

from tests.contracts.spec.replyformexamples.kit import SPEC


class TestReplyRequiredDoc(unittest.TestCase):
    """RULE.md section 1 (Rafael, op-k77qhwzc): the compiler code `reply_required`."""

    def setUp(self) -> None:
        self.dsl = (SPEC / "contract-dsl.md").read_text(encoding="utf-8")

    def test_listed_with_the_required_codes_and_in_the_schema(self) -> None:
        sentence = self.dsl.split("Required codes are ", 1)[1].split(". ", 1)[0]
        self.assertIn("`reply_required`", sentence)
        schema = json.loads(
            (SPEC / "schemas/protocol-v2.schema.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            "reply_required",
            schema["$defs"]["compiler_error"]["properties"]["code"]["enum"],
        )

    def test_static_validation_and_code_table(self) -> None:
        validation = self.dsl.split("## Static validation", 1)[1].split("\n## ", 1)[0]
        bullets = [ln for ln in validation.splitlines() if ln.startswith("- ")]
        self.assertTrue(any("`reply_required`" in ln for ln in bullets))
        last = next(i for i, ln in enumerate(bullets) if "reply_required" in ln)
        clock = next(
            i for i, ln in enumerate(bullets) if "contract_clock_required" in ln
        )
        self.assertLess(
            last, clock, "reply_required runs before contract_clock_required"
        )
        self.assertTrue(
            any(ln.startswith("| `reply_required` |") for ln in validation.splitlines())
        )

    def test_reply_section_shows_the_error_and_its_fix(self) -> None:
        section = self.dsl.split("## Reply alternatives", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`reply_required`", section)
        self.assertRegex(section, r"request\(\) => reviewing")
        self.assertRegex(section, r"request\(\) awaits reviewing")
