from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.band_format import _wildcard


class TestIgnoreGlob(unittest.TestCase):
    def test_only_star_and_question_are_special_and_dotfiles_stay_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ["ab", "axxb", "a\nb", "[ab]x", "ax", ".ab", "éx"]
            for name in names:
                (root / name).touch()
            for pattern, expected in (
                ("a*b", ["ab", "axxb", "a\nb"]),
                ("a?b", ["a\nb"]),
                ("[ab]*", ["[ab]x"]),
                ("?x", ["ax", "éx"]),
                (".a*", [".ab"]),
                ("*a*a*a*a*ab", []),
            ):
                self.assertEqual(
                    sorted(Path(p).name for p in _wildcard(str(root / pattern))),
                    sorted(expected),
                    pattern,
                )
