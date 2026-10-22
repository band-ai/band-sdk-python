from __future__ import annotations

import unittest

import pytest

from tests.contracts.runtime.bandformatubf.kit import ALL_UBF_VECTORS, markdown
from tests.contracts.runtime.formatter import elixir_outputs, python_outputs
from tests.contracts.runtime.settings import runtime_settings


@pytest.mark.contract_elixir
class TestBandFormatUbfCross(unittest.TestCase):
    def test_every_ubf_vector_and_fixed_point_is_byte_identical(self) -> None:
        for kind in ("band", "md"):
            inputs, labels = [], []
            for name, source, expected in ALL_UBF_VECTORS:
                for phase, text in (("input", source), ("expected", expected)):
                    inputs.append((markdown(text) if kind == "md" else text).encode())
                    labels.append(f"{name}/{phase}")
            with self.subTest(kind=kind):
                oracle = elixir_outputs(
                    str(runtime_settings().band_format_ex), inputs, kind
                )
                actual = python_outputs(inputs, kind)
                self.assertEqual(len(oracle), len(inputs))
                self.assertEqual(
                    [label for label, e, a in zip(labels, oracle, actual) if e != a], []
                )
