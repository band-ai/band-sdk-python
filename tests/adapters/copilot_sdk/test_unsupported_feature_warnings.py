"""Supported emit/capabilities values must construct without raising."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapter
from band.core.types import Capability, Emit
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    requires_copilot_sdk,
)

pytestmark = requires_copilot_sdk


class TestUnsupportedFeatureWarnings:
    @pytest.mark.asyncio
    async def test_no_error_for_supported_features(self, recwarn):
        client = FakeCopilotClient()
        adapter = CopilotSDKAdapter(
            client_factory=lambda: client,
            emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
            capabilities=Capability.MEMORY | Capability.CONTACTS,
        )

        await adapter.on_started("Agent", "desc")

        assert not [w for w in recwarn.list if issubclass(w.category, UserWarning)]
