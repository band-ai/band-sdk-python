"""DedupingAgentToolsConstruction behavior."""

from __future__ import annotations

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestDedupingAgentToolsConstruction:
    def test_invalid_ttl_rejected(self):
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), ttl_seconds=0)
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), ttl_seconds=-1)

    def test_invalid_max_entries_rejected(self):
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), max_entries=0)
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), max_entries=-5)
