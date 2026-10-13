"""Agent compositor: ModelSelectionCheckOnStart."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.core.model_catalog import ModelSelection, ModelSelectionError
from tests.catalogs import CatalogAdapter


class TestModelSelectionCheckOnStart:
    """Agent.start() refuses a model selection the adapter's catalog lacks."""

    @pytest.mark.asyncio
    async def test_an_advertised_selection_starts(self, mock_runtime):
        adapter = CatalogAdapter(
            ModelSelection(model="sonnet", reasoning_effort="high")
        )

        await Agent(runtime=mock_runtime, adapter=adapter).start()

        mock_runtime.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_rejected_selection_fails_start_before_connecting(
        self, mock_runtime
    ):
        adapter = CatalogAdapter(ModelSelection(model="gpt-9"))

        with pytest.raises(ModelSelectionError, match='model "gpt-9"'):
            await Agent(runtime=mock_runtime, adapter=adapter).start()

        mock_runtime.start.assert_not_awaited()
        assert adapter.cleaned_up

    @pytest.mark.asyncio
    async def test_a_failing_release_does_not_mask_the_rejection(
        self, mock_runtime, caplog: pytest.LogCaptureFixture
    ):
        adapter = CatalogAdapter(ModelSelection(model="gpt-9"), cleanup_fails=True)

        with pytest.raises(ModelSelectionError, match='model "gpt-9"'):
            await Agent(runtime=mock_runtime, adapter=adapter).start()

        assert "Adapter cleanup_all failed" in caplog.text
