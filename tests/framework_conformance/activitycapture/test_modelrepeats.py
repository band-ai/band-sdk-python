"""Repeated activity executions require distinct observed model calls."""

from __future__ import annotations

import pytest

from tests.e2e.baseline.toolkit.activitywork import (
    ActivityLookupInput,
    ActivityWork,
    Operation,
    repeated_invocation,
)
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer


def tool_calls(
    work: ActivityWork, request: ActivityLookupInput, ids: tuple[str | None, ...]
) -> list[ToolCall]:
    return [ToolCall(work.tool.name, request.model_dump(), call_id) for call_id in ids]


class TestModelRepeats:
    @pytest.mark.parametrize(
        ("room_ids", "provider_ids"),
        [(("first", "second"), ()), ((None, None), ("first", "second"))],
        ids=["room-reports-model-ids", "provider-reports-model-ids"],
    )
    async def test_a_distinct_model_call_explains_each_repeat(
        self,
        held_lookup: tuple[ActivityWork, HeldLookupPeer],
        room_ids: tuple[str | None, ...],
        provider_ids: tuple[str, ...],
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.C, private_note=work.note)
        peer.release.set()
        work[Operation.C].release.set()
        result = await work.execute(request)

        with pytest.raises(ValueError, match=str(repeated_invocation(Operation.C))):
            await work.execute(request)

        work.assert_model_repeats(
            Operation.C,
            tool_calls(work, request, room_ids),
            model_requests=tool_calls(work, request, provider_ids),
        )
        assert await work.completed(Operation.C) == result

    @pytest.mark.parametrize(
        ("call_ids", "provider_ids"),
        [
            (("original", "original"), ("first", "second")),
            (("original",), ()),
            (("original", None), ("first", "second")),
            (("original", ""), ()),
            ((None, None), ("first",)),
            ((None, None), ("first", "first")),
            ((None,), ("first", "second")),
        ],
        ids=[
            "sdk-repeat",
            "missing-event",
            "missing-id",
            "empty-id",
            "single-provider-request",
            "replayed-provider-id",
            "missing-room-call",
        ],
    )
    async def test_an_unattributed_repeat_fails_by_operation_name(
        self,
        held_lookup: tuple[ActivityWork, HeldLookupPeer],
        call_ids: tuple[str | None, ...],
        provider_ids: tuple[str, ...],
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.C, private_note=work.note)
        peer.release.set()
        work[Operation.C].release.set()
        await work.execute(request)

        with pytest.raises(ValueError, match=str(repeated_invocation(Operation.C))):
            await work.execute(request)

        with pytest.raises(AssertionError, match=f"Operation {Operation.C.value} "):
            work.assert_model_repeats(
                Operation.C,
                tool_calls(work, request, call_ids),
                model_requests=tool_calls(work, request, provider_ids),
            )

    async def test_an_unrepeated_execution_needs_no_repeat_evidence(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.C, private_note=work.note)
        peer.release.set()
        work[Operation.C].release.set()
        await work.execute(request)

        work.assert_model_repeats(Operation.C, [])
