"""Supported features must not trigger unsupported-feature warnings."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.core.types import AdapterFeatures, Capability, Emit
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    requires_copilot_sdk,
)

pytestmark = requires_copilot_sdk


class TestUnsupportedFeatureWarnings:
    @pytest.mark.asyncio
    async def test_no_warning_for_supported_features(self, recwarn):
        client = FakeCopilotClient()
        adapter = CopilotSDKAdapter(
            client_factory=lambda: client,
            features=AdapterFeatures(
                emit={Emit.EXECUTION, Emit.THOUGHTS},
                capabilities={Capability.MEMORY, Capability.CONTACTS},
            ),
        )

        await adapter.on_started("Agent", "desc")

        assert not [w for w in recwarn.list if issubclass(w.category, UserWarning)]
