from __future__ import annotations

import unittest

from contracts.tools.formatbenchmark import workloads
from remote_agent import band_format


class TestBenchmarkWorkload(unittest.TestCase):
    def test_native_workloads_have_independent_expected_results(self) -> None:
        for size in (1024, 2048):
            for name, (method, source, expected) in workloads(size).items():
                with self.subTest(size=size, construct=name):
                    self.assertEqual(getattr(band_format, method)(source), expected)
