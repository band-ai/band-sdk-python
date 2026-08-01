"""Adapter turn boundary + oneshot helper."""

from __future__ import annotations

import pytest

from band.runtime.tools import BAND_SEND_MESSAGE
from band.testing import FakeAgentTools
from tests.core.adapterhelpers import RecordingAdapter
from tests.core.contractsupport import PostingAdapter, oneshot, shim_turn


@pytest.mark.asyncio
async def test_turn_mints_delivery_receipt_when_adapter_posts(
    tools: FakeAgentTools,
) -> None:
    async with shim_turn(PostingAdapter(), tools=tools) as turn:
        result = await turn.run()

    assert result.delivery is not None
    assert result.delivery.tool_name == BAND_SEND_MESSAGE
    assert tools.tool_calls[0]["tool_name"] == BAND_SEND_MESSAGE


@pytest.mark.asyncio
async def test_oneshot_drives_adapter_through_run() -> None:
    adapter = RecordingAdapter()

    async with oneshot(adapter, content="hello") as turn:
        result = await turn.run()

    assert adapter.calls[0]["msg"].content == "hello"
    assert result.delivery is None
