from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.band_format import format_markdown_bytes, format_source
from tests.contracts.runtime.bandformatubf.kit import (
    ALL_UBF_VECTORS,
    UBF_VECTORS,
    markdown,
)
from tests.contracts.runtime.formatter import run_cli


class TestBandFormatUbf(unittest.TestCase):
    def test_ubf_vectors_have_canonical_bytes_and_are_fixed_points(self) -> None:
        for name, source, expected in ALL_UBF_VECTORS:
            with self.subTest(vector=name):
                self.assertEqual(format_source(source), expected)
                self.assertEqual(format_source(expected), expected)

    def test_ubf_vectors_in_markdown_preserve_surrounding_bytes(self) -> None:
        for name, source, expected in ALL_UBF_VECTORS:
            with self.subTest(vector=name):
                canonical = markdown(expected).encode()
                self.assertEqual(
                    format_markdown_bytes(markdown(source).encode()), canonical
                )
                self.assertEqual(format_markdown_bytes(canonical), canonical)

    def test_cli_check_is_readonly_then_formats_to_fixed_point(self) -> None:
        for suffix, wrap in (("band", lambda text: text), ("md", markdown)):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("reply." + suffix)
                source, expected = map(wrap, UBF_VECTORS[5][1:])
                path.write_text(source)
                checked = run_cli("--check", str(path))
                self.assertEqual(checked.returncode, 1, checked.stderr)
                self.assertEqual(path.read_text(), source)
                result = run_cli(str(path))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(path.read_text(), expected)
                checked = run_cli("--check", str(path))
                self.assertEqual(checked.returncode, 0, checked.stderr)
