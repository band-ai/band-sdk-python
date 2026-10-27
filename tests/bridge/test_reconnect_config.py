"""ReconnectConfig behavior."""

from __future__ import annotations

import pytest
from bridge_core.config import (
    ReconnectConfig,
)


class TestReconnectConfig:
    def test_defaults(self) -> None:
        c = ReconnectConfig()
        assert c.initial_delay == 1.0
        assert c.max_delay == 60.0
        assert c.multiplier == 2.0

    def test_rejects_non_positive_initial_delay(self) -> None:
        with pytest.raises(ValueError, match="initial_delay"):
            ReconnectConfig(initial_delay=0)

    def test_rejects_multiplier_below_one(self) -> None:
        with pytest.raises(ValueError, match="multiplier"):
            ReconnectConfig(multiplier=0.5)
