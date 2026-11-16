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


class TestModelRepeats:
    async def test_a_distinct_model_call_explains_each_repeat(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
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
            [
                ToolCall(work.tool.name, request.model_dump(), tool_call_id=call_id)
                for call_id in ("original", "model-repeat")
            ],
        )
        assert await work.completed(Operation.C) == result

    @pytest.mark.parametrize(
        "call_ids",
        [
            ("original", "original"),
            ("original",),
            ("original", None),
            ("original", ""),
        ],
        ids=["sdk-repeat", "missing-event", "missing-id", "empty-id"],
    )
    async def test_an_unattributed_repeat_fails_by_operation_name(
        self,
        held_lookup: tuple[ActivityWork, HeldLookupPeer],
        call_ids: tuple[str | None, ...],
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.C, private_note=work.note)
        peer.release.set()
        work[Operation.C].release.set()
        await work.execute(request)

        with pytest.raises(ValueError, match=str(repeated_invocation(Operation.C))):
            await work.execute(request)

        observed = [
            ToolCall(work.tool.name, request.model_dump(), tool_call_id=call_id)
            for call_id in call_ids
        ]
        with pytest.raises(AssertionError, match=f"Operation {Operation.C.value} "):
            work.assert_model_repeats(Operation.C, observed)

    async def test_an_unrepeated_execution_needs_no_repeat_evidence(
        self, held_lookup: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = held_lookup
        request = ActivityLookupInput(key=Operation.C, private_note=work.note)
        peer.release.set()
        work[Operation.C].release.set()
        await work.execute(request)

        work.assert_model_repeats(Operation.C, [])
