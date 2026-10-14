"""A batch turn runs through the adapter's one turn builder and the one judge."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError, InputNotAccepted
from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.core.batchturnkit import (
    Entry,
    RefusingAdapter,
    TurnBuilderAdapter,
    UnbuiltAdapter,
    UndeclaredAdapter,
    batch_input,
)
from tests.framework_conformance.invocationkit import turn_input
from tests.runtime.conftest import platform_msg


class TestBatchTurn:
    async def test_an_adapter_that_never_declared_batch_support_refuses_a_batch(
        self,
    ) -> None:
        inp = batch_input()

        with pytest.raises(BandConfigError, match="UndeclaredAdapter does not support"):
            await UndeclaredAdapter().on_messages(inp)

    @pytest.mark.parametrize(
        ("entry", "message_ids"),
        [
            pytest.param(SimpleAdapter.on_event, ("m1",), id="singleton"),
            pytest.param(SimpleAdapter.on_messages, ("m1", "m2"), id="batch"),
        ],
    )
    async def test_every_entry_reaches_the_one_turn_builder(
        self, entry: Entry, message_ids: tuple[str, ...]
    ) -> None:
        adapter = TurnBuilderAdapter(declines=True)
        inp = turn_input(*(platform_msg(message_id) for message_id in message_ids))

        await entry(adapter, inp)

        assert adapter.turns == [message_ids]

    async def test_a_batch_that_did_nothing_is_reported_once_and_failed(self) -> None:
        inp = batch_input()

        with pytest.raises(TurnResultAlreadyReported):
            await TurnBuilderAdapter(declines=False).on_messages(inp)

        assert failure_reports(inp.tools) == [MISSING_REPLY_FAILURE]

    async def test_a_batch_that_declined_as_a_whole_completes(self) -> None:
        inp = batch_input()

        await TurnBuilderAdapter(declines=True).on_messages(inp)

        assert failure_reports(inp.tools) == []

    async def test_a_batch_the_provider_refused_is_not_judged(self) -> None:
        inp = batch_input()

        with pytest.raises(InputNotAccepted):
            await RefusingAdapter(declines=False).on_messages(inp)

        assert failure_reports(inp.tools) == []
        assert not inp.tools.turn.judged

    async def test_an_adapter_that_declares_batch_without_a_builder_fails_loudly(
        self,
    ) -> None:
        inp = batch_input()

        with pytest.raises(BandConfigError, match="on_turn"):
            await UnbuiltAdapter().on_messages(inp)
