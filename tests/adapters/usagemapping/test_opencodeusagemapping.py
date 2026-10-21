from __future__ import annotations

from band.core.types import TurnUsage
from band.integrations.opencode import OpencodeMessageInfo


class TestOpencodeUsageMapping:
    def test_maps_info_tokens_with_nested_cache(self):
        """OpenCode assistant info.tokens (nested cache) maps onto TurnUsage.

        OpenCode reports reasoning disjointly from output (its own total is
        input + output + reasoning + cache), so reasoning folds into
        output_tokens: 20 + 7 = 27.
        """
        info = OpencodeMessageInfo.model_validate(
            {
                "tokens": {
                    "input": 100,
                    "output": 20,
                    "reasoning": 7,
                    "cache": {"read": 5, "write": 3},
                }
            }
        )
        assert info.tokens is not None
        assert info.tokens.to_turn_usage() == TurnUsage(
            input_tokens=100,
            output_tokens=27,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_or_malformed_tokens_is_empty(self):
        """No tokens (or a non-dict tokens) parses as absent usage."""
        assert OpencodeMessageInfo.model_validate({}).tokens is None
        assert OpencodeMessageInfo.model_validate({"tokens": "nope"}).tokens is None
