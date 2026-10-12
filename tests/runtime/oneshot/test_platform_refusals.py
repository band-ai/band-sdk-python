"""OneShotInvoker: refused claims and acknowledgements."""

from __future__ import annotations

import pytest

from band.core.exceptions import (
    BandConnectionError,
    RoomExecutionStoppedError,
)
from band.runtime.oneshot import (
    OneShotStatus,
)
from tests.runtime.conftest import ctx_item, make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body


@pytest.mark.parametrize("refusal", [False, "stopped"])
async def test_refused_claim_never_invokes_adapter(refusal: bool | str) -> None:
    adapter = make_adapter_mock()
    link = make_link_mock(next_messages=[platform_msg("m1")])
    if refusal == "stopped":
        link.mark_processing.side_effect = RoomExecutionStoppedError("r1")
        error = RoomExecutionStoppedError
    else:
        link.mark_processing.return_value = False
        error = BandConnectionError
    invoker = await make_invoker(link, adapter)
    with pytest.raises(error):
        await invoker.handle_event(msg_body(msg_id="m1"))
    adapter.on_event.assert_not_awaited()
    link.mark_failed.assert_not_awaited()


@pytest.mark.parametrize("refusal", [False, "stopped"])
async def test_refused_completion_is_not_done_or_provider_failure(
    refusal: bool | str,
) -> None:
    link = make_link_mock(next_messages=[platform_msg("m1")])
    if refusal == "stopped":
        link.mark_processed.side_effect = RoomExecutionStoppedError("r1")
        error = RoomExecutionStoppedError
    else:
        link.mark_processed.return_value = False
        error = BandConnectionError
    invoker = await make_invoker(link)
    with pytest.raises(error):
        await invoker.handle_event(msg_body(msg_id="m1"))
    link.mark_failed.assert_not_awaited()


@pytest.mark.parametrize("operation", ["claim", "ack"])
async def test_drain_records_only_accepted_messages(operation: str) -> None:
    link = make_link_mock(
        history_items=[ctx_item("m2"), ctx_item("m3")],
        next_messages=[platform_msg("m1"), platform_msg("m2"), platform_msg("m3")],
    )
    mark = link.mark_processing if operation == "claim" else link.mark_processed
    mark.side_effect = [True, True, RoomExecutionStoppedError("r1")]
    invoker = await make_invoker(link)
    result = await invoker.handle_event(msg_body(msg_id="m1"))
    assert result["status"] == OneShotStatus.DONE
    assert result["drained"] == ["m2"]
