"""DeckParity specification regression checks."""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import ClassVar

import pytest

from contracts.spec.tools import bandhighlight as bh
from tests.contracts.spec.bandhighlight.kit import PROBE, TOOLS, band_blocks


class TestDeckParity(unittest.TestCase):
    """Deck mode is the deck's own function, byte for byte."""

    EDGES: ClassVar[list[str]] = [
        "STATE new\n  DUE all OF {a,b} WITH review IN 5m => stalled\n",
        "state s\n  a -> b: k() activity @5m => x | y\n  collect r from a -> b @1m => c\n    failure f()\n",
        '%% note\n/* block */ ~S"""raw""" ~r/a\\/b/ \'q\' "s\\"" $req @self 3 7d ..|=:',
        "contract @infinity max @4h => expired\non x offline for 5m => y\ntype t: agent\n  ok: boolean\n",
    ]

    @pytest.mark.contract_node
    def test_deck_mode_matches_the_vendored_deck_function(self) -> None:
        corpus = [src for _, _, src in band_blocks()] + [PROBE] + self.EDGES
        script = (
            "const d=require(process.argv[1]);let s='';process.stdin.on('data',c=>s+=c);"
            "process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).map(d.highlightContract))))"
        )
        out = subprocess.run(
            ["node", "-e", script, str(TOOLS / "band_highlight_deck.js")],
            input=json.dumps(corpus),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for source, expected in zip(corpus, json.loads(out), strict=True):
            with self.subTest(source=source[:60]):
                self.assertEqual(bh.highlight(source, deck=True), expected)
