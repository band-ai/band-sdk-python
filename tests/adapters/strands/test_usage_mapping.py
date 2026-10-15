from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest

pytest.importorskip("strands", reason="strands extra not installed")


from band.adapters.strands import (
    StrandsAdapter,
)
from band.core.types import (
    TurnUsage,
)


class TestUsageMapping:
    def test_usage_from_agent_maps_all_fields(self):
        class _Metrics:
            accumulated_usage: ClassVar[dict[str, int]] = {
                "inputTokens": 10,
                "outputTokens": 5,
                "totalTokens": 15,
                "cacheReadInputTokens": 3,
                "cacheWriteInputTokens": 2,
            }

        class _Agent:
            event_loop_metrics = _Metrics()

        usage = StrandsAdapter._usage_from_agent(cast("Any", _Agent()))

        assert usage == TurnUsage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=3,
            cache_write_tokens=2,
        )
