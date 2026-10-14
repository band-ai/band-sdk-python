"""A batch turn runs through the adapter's one turn builder and the one judge."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from band.core.exceptions import BandConfigError, InputNotAccepted
from band.core.protocols import AgentToolsProtocol, TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from band.testing.fake_tools import FakeAgentTools


def original(message_id: str, sender_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=message_id,
        room_id="room-1",
        content=message_id,
        sender_id=sender_id,
        sender_type="User",
        sender_name=None,
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def turn_input(*messages: PlatformMessage) -> AgentInput:
    return AgentInput(
        msg=messages[0],
        messages=messages,
        tools=FakeAgentTools(room_id="room-1"),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )


class LegacyAdapter(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None: ...


class BatchAdapter(SimpleAdapter[Any]):
    """One turn builder behind both entry points; declines when told to."""

    supports_batch_input = True

    def __init__(self, *, declines: bool) -> None:
        super().__init__()
        self.declines = declines
        self.turns: list[tuple[str, ...]] = []

    async def on_message(self, msg: PlatformMessage, *args: Any, **kwargs: Any) -> None:
        await self.on_turn((msg,), *args, **kwargs)

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.turns.append(tuple(m.id for m in messages))
        if self.declines:
            await tools.no_reply("nothing owed")


class RejectingAdapter(BatchAdapter):
    async def on_turn(self, *args: Any, **kwargs: Any) -> None:
        raise InputNotAccepted("provider refused the input")


class TestBatchTurn:
    async def test_an_adapter_that_never_declared_batch_support_refuses_a_batch(
        self,
    ) -> None:
        inp = turn_input(original("m1", "alice"), original("m2", "bob"))

        with pytest.raises(BandConfigError, match="LegacyAdapter does not support"):
            await LegacyAdapter().on_messages(inp)

    @pytest.mark.parametrize(
        "messages",
        [
            [original("m1", "alice")],
            [original("m1", "alice"), original("m2", "bob")],
        ],
        ids=["singleton", "batch"],
    )
    async def test_every_entry_reaches_the_one_turn_builder(
        self, messages: list[PlatformMessage]
    ) -> None:
        adapter = BatchAdapter(declines=True)
        inp = turn_input(*messages)

        if len(messages) > 1:
            await adapter.on_messages(inp)
        else:
            await adapter.on_event(inp)

        assert adapter.turns == [tuple(m.id for m in messages)]

    async def test_a_batch_that_did_nothing_is_reported_once_and_failed(self) -> None:
        inp = turn_input(original("m1", "alice"), original("m2", "bob"))

        with pytest.raises(TurnResultAlreadyReported):
            await BatchAdapter(declines=False).on_messages(inp)

        assert failure_reports(inp.tools) == [MISSING_REPLY_FAILURE]

    async def test_a_batch_that_declined_as_a_whole_completes(self) -> None:
        inp = turn_input(original("m1", "alice"), original("m2", "bob"))

        await BatchAdapter(declines=True).on_messages(inp)

        assert failure_reports(inp.tools) == []

    async def test_a_batch_the_provider_refused_is_not_judged(self) -> None:
        inp = turn_input(original("m1", "alice"), original("m2", "bob"))

        with pytest.raises(InputNotAccepted):
            await RejectingAdapter(declines=False).on_messages(inp)

        assert failure_reports(inp.tools) == []
        assert not inp.tools.turn.judged

    async def test_an_adapter_that_declares_batch_without_a_builder_fails_loudly(
        self,
    ) -> None:
        class Unbuilt(LegacyAdapter):
            supports_batch_input = True

        inp = turn_input(original("m1", "alice"), original("m2", "bob"))

        with pytest.raises(BandConfigError, match="on_turn"):
            await Unbuilt().on_messages(inp)
