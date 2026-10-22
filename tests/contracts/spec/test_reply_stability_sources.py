"""Historical stability checks retain source identity after the repository move."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.spec.tools import runreplyvectors


class TestReplyStabilitySources(unittest.TestCase):
    def test_archived_sources_match_every_retained_pin(self) -> None:
        pins = json.loads(runreplyvectors.PINS.read_text())
        sources = runreplyvectors.fixtures(pins["base"])
        self.assertEqual(
            {row["path"]: row["source"]["sha256"] for row in sources},
            {row["path"]: row["source_sha256"] for row in pins["fixtures"]},
        )

    def test_changed_archive_is_refused_before_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "sources.json"
            altered.write_bytes(runreplyvectors.STABILITY_SOURCES.read_bytes() + b"\n")
            with (
                patch.object(runreplyvectors, "STABILITY_SOURCES", altered),
                self.assertRaisesRegex(
                    AssertionError, "Historical stability sources changed"
                ),
            ):
                runreplyvectors.fixtures(runreplyvectors.STABILITY_BASE)

    def test_source_revision_must_match_the_requested_pins(self) -> None:
        with self.assertRaisesRegex(AssertionError, "revision differs from the pins"):
            runreplyvectors.fixtures("wrong-base")

    def test_existing_stability_pins_cannot_be_rewritten(self) -> None:
        before = runreplyvectors.PINS.read_bytes()
        with self.assertRaisesRegex(SystemExit, "pins are written once"):
            runreplyvectors.stability(None, True)
        self.assertEqual(runreplyvectors.PINS.read_bytes(), before)
