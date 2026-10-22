"""MutationGuard specification regression checks."""

from __future__ import annotations

import ast
import unittest

from contracts.spec.tools import checkv2
from tests.contracts.spec.checkerfailures.kit import ROOT


class TestMutationGuard(unittest.TestCase):
    """checkv2 mutated sources with bare str.replace: an absent needle compiled the same source twice (impl M6)."""

    def test_an_absent_needle_is_refused(self) -> None:
        self.assertEqual(checkv2.swap("a ~W[b]", "~W[", "~w["), "a ~w[b]")
        with self.assertRaises(ValueError):
            checkv2.swap("a ~w[b]", "~W[", "~w[")

    def test_every_source_mutation_is_guarded(self) -> None:
        tree = ast.parse((ROOT / "spec/tools/checkv2.py").read_text())
        bare = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
        ]
        self.assertEqual(bare, [])
