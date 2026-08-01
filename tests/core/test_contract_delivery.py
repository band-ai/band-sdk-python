"""DeliveryReceipt derivation — evidence rules that gate reply fallback."""

from __future__ import annotations

import pytest

from band.core.contracts.delivery import (
    DeliveryReceipt,
    receipt_from_acp_chunks,
    receipt_from_tool_outcome,
)
from band.integrations.acp.types import CollectedChunk
from band.runtime.tools import BAND_LIST_CONTACTS, BAND_SEND_MESSAGE, ToolCallOutcome
from tests.core.contractsupport import (
    acp_completed_post,
    acp_correlated_post,
    acp_failed_post,
    acp_non_posting_tool,
    acp_uncorrelated_post,
)


def test_in_process_receipt_requires_ok_room_posting_tool() -> None:
    ok = ToolCallOutcome(value={}, ok=True)
    fail = ToolCallOutcome(value=None, ok=False, error_message="nope")

    assert receipt_from_tool_outcome(BAND_SEND_MESSAGE, ok) == DeliveryReceipt(
        tool_name=BAND_SEND_MESSAGE
    )
    assert receipt_from_tool_outcome(BAND_SEND_MESSAGE, fail) is None
    assert receipt_from_tool_outcome(BAND_LIST_CONTACTS, ok) is None


@pytest.mark.parametrize(
    ("chunks", "replied"),
    [
        pytest.param(acp_completed_post(), True, id="completed-call"),
        pytest.param(acp_correlated_post(), True, id="correlated-result"),
        pytest.param(acp_failed_post(), False, id="failed-post"),
        pytest.param(acp_non_posting_tool(), False, id="non-posting-tool"),
        pytest.param(acp_uncorrelated_post(), False, id="empty-ids-never-correlate"),
    ],
)
def test_acp_receipt_needs_a_completed_room_post(
    chunks: list[CollectedChunk], replied: bool
) -> None:
    receipt = receipt_from_acp_chunks(chunks)

    assert (receipt is not None) is replied
    if replied:
        assert receipt is not None
        assert receipt.tool_name == BAND_SEND_MESSAGE
