"""ModelFailuresReturnedAsData behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest

from tests.adapters.genaikit import (
    ModelFailureCases,
    no_candidates,
)


class TestModelFailuresReturnedAsData(ModelFailureCases):
    PROVIDER = "google_adk"

    @pytest.mark.asyncio
    async def test_no_candidates_fails_with_adks_own_code(self, scripted_adapter):
        await self.assert_turn_fails_with(
            scripted_adapter, [no_candidates()], "UNKNOWN_ERROR"
        )
