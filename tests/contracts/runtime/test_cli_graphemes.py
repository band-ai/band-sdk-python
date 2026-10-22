from __future__ import annotations

import hashlib
import json
import unittest

from remote_agent.graphemes import boundaries, count, table_data
from tests.paths import CONTRACTS_ROOT


class TestCLIGrapheme(unittest.TestCase):
    def test_df44_all_official_unicode16_boundaries_and_counts(self) -> None:
        source = CONTRACTS_ROOT / "header-spikes/priv/unicode16"
        cases = 0
        for line in (source / "GraphemeBreakTest.txt").read_text().splitlines():
            tokens = line.split("#", 1)[0].split()
            if not tokens:
                continue
            scalars, expected = [], []
            for token in tokens:
                if token == "÷":
                    expected.append(len(scalars))
                elif token != "×":
                    scalars.append(chr(int(token, 16)))
            text = "".join(scalars)
            with self.subTest(case=cases):
                self.assertEqual(boundaries(text), expected)
                self.assertEqual(count(text), len(expected) - 1)
            cases += 1
        self.assertEqual(cases, 1093)
        self.assertEqual(boundaries(""), [0])
        self.assertEqual(count(""), 0)

    def test_df44_packaged_table_provenance_matches_engine_sources(self) -> None:
        data = table_data()
        self.assertEqual(data["unicode"], "16.0.0")
        source = CONTRACTS_ROOT / "header-spikes/priv/unicode16"
        manifest = json.loads((source / "manifest.json").read_text())
        self.assertEqual(data["sources"], manifest["sources"])
        for item in data["sources"]:
            self.assertEqual(
                hashlib.sha256((source / item["path"]).read_bytes()).hexdigest(),
                item["sha256"],
            )
