"""Snippet specification regression checks."""

from __future__ import annotations

import json
import unittest

from contracts.spec.tools import oldformguard as guard
from tests.contracts.spec.oldformguard.kit import OLD, scan_files


class TestSnippet(unittest.TestCase):
    def test_every_carrier_is_scanned_with_its_file_line(self) -> None:
        indented = "\n".join("    " + line for line in OLD.split("\n"))
        escaped = json.dumps(OLD)
        files = {
            "a.band": OLD,
            "doc.md": "Text\n\n```text\n" + OLD + "```\n\nMore\n\n" + indented + "\n",
            "contracts/skills/x/SKILL.md": "~~~\n" + OLD + "~~~\n",
            "test/a_test.exs": 'defmodule A do\n  @body """\n'
            + "\n".join("  " + l for l in OLD.split("\n"))
            + '"""\n  @one '
            + escaped
            + "\nend\n",
            "tools/a.py": "BODY = ("
            + " ".join(json.dumps(l + "\n") for l in OLD.split("\n"))
            + ")\n",
            "tools/b.py": 'BODY = """\n' + OLD + '"""\n',
            "vectors.json": '{\n "cases": [\n  {"source": ' + escaped + "}\n ]\n}\n",
            "notes.txt": OLD,
        }
        found = {(h["path"], h["line"]) for h in scan_files(files)}
        self.assertEqual(
            found,
            {
                ("a.band", 2),
                ("doc.md", 5),
                ("doc.md", 14),
                ("contracts/skills/x/SKILL.md", 3),
                ("test/a_test.exs", 4),
                ("test/a_test.exs", 9),
                ("tools/a.py", 1),
                ("tools/b.py", 3),
                ("vectors.json", 3),
                ("notes.txt", 2),
            },
        )

    def test_an_elixir_heredoc_opened_on_a_line_of_its_own_is_scanned(self) -> None:
        def body(indent: str) -> str:
            return (
                "".join(indent + line + "\n" for line in OLD.rstrip("\n").split("\n"))
                + indent
                + '"""'
            )

        files = {
            "test/b_test.exs": 'defmodule B do\n  def c(body \\\\ nil) do\n    body ||\n      """\n'
            + body(" " * 6)
            + '\n  end\n  @two """\n'
            + body("  ")
            + " |> String.trim()\nend\n"
        }
        self.assertEqual({h["line"] for h in scan_files(files)}, {6, 14})

    def test_two_fences_with_the_same_state_names_are_separate_contracts(self) -> None:
        first = OLD.replace("  reviewer -> lead: review() => done\n", "")
        second = OLD.replace("  lead -> reviewer: request() => reviewing\n", "")
        self.assertEqual(
            scan_files(
                {"doc.md": "```band\n" + first + "```\n\n```band\n" + second + "```\n"}
            ),
            [],
        )

    def test_saved_run_logs_and_evidence_are_records_not_examples(self) -> None:
        self.assertEqual(
            scan_files(
                {
                    "run.log": OLD,
                    "contracts/agent-runtime/evidence/x.md": "```\n" + OLD + "```\n",
                }
            ),
            [],
        )

    def test_pinned_band_v1_fixtures_retain_their_original_grammar(self) -> None:
        files = {
            "tests/contracts/runtime/fixtures/band/saved.band.txt": OLD,
            "tests/contracts/runtime/test_band_quality_authoring.py": 'SOURCE = """\n'
            + OLD
            + '"""\n',
        }
        self.assertEqual(scan_files(files), [])

    def test_only_the_exact_historical_source_archive_is_exempt(self) -> None:
        archived = "contracts/spec/conformance/reply-stability-sources.json"
        current = "contracts/spec/conformance/reply-stability-current.json"
        content = json.dumps({"source": OLD})
        self.assertEqual(
            {hit["path"] for hit in scan_files({archived: content, current: content})},
            {current},
        )

    def test_frozen_engine_sources_do_not_exempt_current_engine_sources(self) -> None:
        archived = "contracts/engines/band-v1/contract-lab/contracts/old.band"
        current = "contracts/contract-lab/contracts/current.band"
        self.assertEqual(
            {hit["path"] for hit in scan_files({archived: OLD, current: OLD})},
            {current},
        )

    def test_adjacent_current_sources_still_require_reply_heads(self) -> None:
        files = {
            "tests/contracts/runtime/fixtures/current/saved.band.txt": OLD,
            "tests/contracts/runtime/fixtures/band-current/saved.band.txt": OLD,
            "tests/contracts/runtime/test_band_quality_authoring_extra.py": 'SOURCE = """\n'
            + OLD
            + '"""\n',
        }
        self.assertEqual({hit["path"] for hit in scan_files(files)}, set(files))

    def test_owners_follow_the_coverage_map(self) -> None:
        self.assertEqual(guard.owner("contracts/spec/walkthrough.md"), "Theo")
        self.assertEqual(guard.owner("contracts/spec/defaults-v2.md"), "Ivy")
        self.assertEqual(guard.owner("contracts/IMPLEMENTATION-EXPLAINER.md"), "Ivy")
        self.assertEqual(
            guard.owner("contracts/contract-lab/test/fixtures/room-work-v030.band"),
            "Rafael",
        )
        self.assertEqual(
            guard.owner("contracts/contract-lab/test/state_total_test.exs"), "Priya"
        )
        self.assertEqual(
            guard.owner("contracts/spec/conformance/semantic-inputs-v2.json"),
            "Rafael (generated JSON)",
        )
