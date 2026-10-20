from __future__ import annotations

from types import SimpleNamespace

from band.adapters.codex import CodexAdapter
from band.core.types import TurnUsage


class TestCodexUsageMapping:
    def test_maps_per_turn_deltas(self):
        """Codex's per-turn token deltas (not thread cumulatives) map to TurnUsage.

        Codex reports reasoning disjointly from output (its own total is
        input + output + reasoning), so reasoning folds into output_tokens:
        42 + 8 = 50.
        """
        usage = SimpleNamespace(
            turn_input_tokens=130, turn_output_tokens=42, turn_reasoning_tokens=8
        )
        assert CodexAdapter._turn_usage(usage) == TurnUsage(
            input_tokens=130, output_tokens=50
        )

    def test_none_usage_is_empty(self):
        """No usage object for the thread yields empty usage (no emit)."""
        assert CodexAdapter._turn_usage(None) == TurnUsage()
