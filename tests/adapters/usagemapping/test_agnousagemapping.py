from __future__ import annotations

from types import SimpleNamespace

from band.adapters.agno import AgnoAdapter
from band.core.types import TurnUsage


class TestAgnoUsageMapping:
    def test_maps_run_metrics(self):
        """RunOutput.metrics (aggregated) maps onto TurnUsage; names line up."""
        metrics = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )
        response = SimpleNamespace(metrics=metrics)
        assert AgnoAdapter._usage_from_response(response) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_metrics_is_empty(self):
        """A RunOutput without metrics yields empty usage."""
        assert (
            AgnoAdapter._usage_from_response(SimpleNamespace(metrics=None))
            == TurnUsage()
        )
