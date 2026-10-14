"""Only an adapter whose rooms share one capacity advertises the optional contract."""

from __future__ import annotations

import pytest

from band.adapters.cursor_acp import CursorACPAdapter
from band.core.protocols import CapacityWaitingAdapter
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)


class TestCapacityWaitingContract:
    @pytest.mark.parametrize(
        "adapter",
        [
            CursorACPAdapter(),
            ACPClientAdapter(ACPClientAdapterConfig(command="agent")),
        ],
        ids=["cursor", "generic-acp"],
    )
    def test_independent_rooms_share_no_capacity_to_wait_for(
        self, adapter: object
    ) -> None:
        assert not isinstance(adapter, CapacityWaitingAdapter)
