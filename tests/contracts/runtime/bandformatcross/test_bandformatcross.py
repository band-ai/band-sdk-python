from __future__ import annotations

import pytest

from tests.contracts.runtime.formatter import (
    EDGE_CASES,
    MD_EDGE_CASES,
    BandFormatCrossFixture,
    band_files,
    markdown_files,
    vector_inputs,
)
from tests.paths import CONTRACTS_ROOT


@pytest.mark.contract_elixir
class TestBandFormatCross(BandFormatCrossFixture):
    def test_every_repo_band_file_is_byte_identical(self) -> None:
        files = band_files()
        self.assertGreaterEqual(len(files), 40)
        root = CONTRACTS_ROOT
        self.compare(
            [str(p.relative_to(root)) for p in files], [p.read_bytes() for p in files]
        )

    def test_edge_cases_are_byte_identical(self) -> None:
        self.compare(
            [repr(c[:40]) for c in EDGE_CASES], [c.encode() for c in EDGE_CASES]
        )

    def test_non_ascii_frontmatter_is_byte_identical(self) -> None:
        source = "---\nname: x\ndescription: caf\u00e9 \u00fcn\u00efc\u00f6d\u00e9\n---\ncontract @1h => expired\n"
        self.compare(["non-ASCII frontmatter"], [source.encode()])

    def test_every_repo_markdown_file_is_byte_identical(self) -> None:
        files = markdown_files()
        self.assertGreaterEqual(len(files), 60)
        root = CONTRACTS_ROOT
        self.compare(
            [str(p.relative_to(root)) for p in files],
            [p.read_bytes() for p in files],
            "md",
        )

    def test_markdown_edge_cases_are_byte_identical(self) -> None:
        self.compare(
            [repr(c[:40]) for c in MD_EDGE_CASES],
            [c.encode() for c in MD_EDGE_CASES],
            "md",
        )

    def test_shared_vectors_are_byte_identical(self) -> None:
        for kind in ("band", "md"):
            cases = sorted(
                p
                for p in vector_inputs() + vector_inputs("expected")
                if p.suffix == "." + kind
            )
            with self.subTest(kind=kind):
                self.assertTrue(cases)
                self.compare(
                    [str(p) for p in cases], [p.read_bytes() for p in cases], kind
                )
