from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from remote_agent.band_format import missing_prompts
from tests.contracts.runtime.bandpromptswaits.kit import LUCAS_PROMPT_CASES, case
from tests.contracts.runtime.formatter import run_cli


class TestBandWaitPrompts(unittest.TestCase):
    def test_lucas_five_independent_prompt_expectations(self) -> None:
        self.assertEqual(len(LUCAS_PROMPT_CASES), 5)
        for vector in LUCAS_PROMPT_CASES:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(
                    missing_prompts(vector["input"].encode()), vector["missing"]
                )

    def test_named_wait_without_section_is_reported_at_head(self) -> None:
        vector = case("implied-undocumented")
        source = vector["input"].replace(
            "request()\n", "request() as req awaits reviewing\n"
        )
        expected = [dict(vector["missing"][0], name="reviewing")]
        self.assertEqual(missing_prompts(source.encode()), expected)

    def test_alternative_prompt_does_not_document_the_head_or_wait(self) -> None:
        vector = case("implied-undocumented")
        source = vector["input"].replace(
            "    => review()", '    "Reply, not wait."\n    => review()'
        )
        self.assertEqual(missing_prompts(source.encode()), vector["missing"])

    def test_comments_notes_and_blanks_do_not_detach_a_wait_from_its_head(self) -> None:
        vector = case("implied-undocumented")
        source = vector["input"].replace(
            "    => review()", "\n%% next alternative\n/* note\n */\n    => review()"
        )
        self.assertEqual(missing_prompts(source.encode()), vector["missing"])

    def test_explicit_section_before_head_still_wins(self) -> None:
        source = case("section-undocumented")["input"]
        section = "state reviewing max_entries 2 => stalled\n"
        source = source.replace(section, "").replace(
            '"""\nNothing has been asked yet.',
            section + '"""\nNothing has been asked yet.',
        )
        body = source.split("---\n", 2)[2]
        line = body.splitlines().index(section.rstrip("\n")) + 1
        self.assertEqual(
            missing_prompts(source.encode()),
            [
                {
                    "item": "state",
                    "name": "reviewing",
                    "line": line,
                    "file_line": line + 6,
                }
            ],
        )

    def test_ordinary_send_does_not_invent_a_wait(self) -> None:
        vector = case("implied-undocumented")
        source = vector["input"].replace(
            "    => review() & collected\n     | @5m & stalled\n", ""
        )
        self.assertEqual(missing_prompts(source.encode()), [])

    def test_cli_require_prompts_refuses_undocumented_wait_without_writing(
        self,
    ) -> None:
        vector = case("implied-undocumented")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wait.band"
            path.write_text(vector["input"])
            result = run_cli("--require-prompts", str(path))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(
                json.loads(result.stderr)["error"]["missing"], vector["missing"]
            )
            self.assertEqual(path.read_text(), vector["input"])
