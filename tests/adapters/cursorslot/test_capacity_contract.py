"""Only adapters that arbitrate shared capacity advertise the optional contract."""

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
        ("adapter", "waits_for_capacity"),
        [
            (CursorACPAdapter(), True),
            (ACPClientAdapter(ACPClientAdapterConfig(command="agent")), False),
        ],
        ids=["cursor", "generic-acp"],
    )
    def test_the_host_detects_the_contract_only_where_it_is_implemented(
        self, adapter: object, waits_for_capacity: bool
    ) -> None:
        assert isinstance(adapter, CapacityWaitingAdapter) is waits_for_capacity
