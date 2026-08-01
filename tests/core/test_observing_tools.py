"""ObservingTools delivery flows — receipt minting and cross-turn isolation."""

from __future__ import annotations

import asyncio

import pytest

from band.core.turn.observing import ObservingTools
from band.core.wrapping import ToolsWrapper
from band.runtime.tools import (
    BAND_LIST_CONTACTS,
    BAND_SEND_MESSAGE,
    LEGACY_CREATE_AGENT_CHAT_MESSAGE,
)
from band.testing import FakeAgentTools

from tests.baseline.delivery.checks import Turn, delivery_of
from tests.baseline.delivery.tools import ROOM_ID, apply_legacy_room_post


@pytest.mark.asyncio
async def test_successful_tool_post_mints_one_receipt() -> None:
    turn = Turn.open()
    await turn.post("a")
    receipt = turn.delivery.assert_delivered()

    await turn.post("b")
    assert turn.delivery.receipt is receipt


@pytest.mark.asyncio
async def test_execute_tool_call_alias_also_mints_once() -> None:
    """Plain ``execute_tool_call`` shares the structured receipt path."""
    turn = Turn.open()
    await turn.observing.execute_tool_call(
        BAND_SEND_MESSAGE, {"content": "a", "mentions": ["Ada"]}
    )
    receipt = turn.delivery.assert_delivered()
    await turn.observing.execute_tool_call(
        BAND_SEND_MESSAGE, {"content": "b", "mentions": ["Ada"]}
    )
    assert turn.delivery.receipt is receipt


@pytest.mark.asyncio
async def test_direct_send_message_is_a_delivery() -> None:
    turn = Turn.open()
    result = await turn.observing.send_message("hi", mentions=[{"id": "u1"}])

    assert result.content == "hi"
    assert turn.inner.messages_sent[0].content == "hi"
    turn.delivery.assert_delivered()


@pytest.mark.asyncio
async def test_failed_send_message_is_not_a_delivery() -> None:
    """A post that raised delivered nothing — do not suppress on it."""

    class Boom(FakeAgentTools):
        async def send_message(self, content, mentions=None):
            raise RuntimeError("403")

    observing = ObservingTools(_inner=Boom(room_id=ROOM_ID))
    with pytest.raises(RuntimeError):
        await observing.send_message("hi", mentions=["Ada"])
    delivery_of(observing).assert_undelivered()


@pytest.mark.asyncio
async def test_failed_structured_room_post_is_not_a_delivery() -> None:
    turn = Turn.open(fail_room_post=True)
    await turn.post("x")
    turn.delivery.assert_undelivered()


@pytest.mark.asyncio
async def test_success_after_failure_mints_once() -> None:
    turn = Turn.open(fail_room_post=True)
    await turn.post("x")
    turn.delivery.assert_undelivered()

    turn.observing._inner = FakeAgentTools(room_id=ROOM_ID)
    await turn.post("y")
    receipt = turn.delivery.assert_delivered()
    await turn.post("z")
    assert turn.delivery.receipt is receipt


@pytest.mark.asyncio
async def test_legacy_room_post_spelling_is_a_delivery() -> None:
    turn = Turn.open()
    await apply_legacy_room_post(turn.observing)
    turn.delivery.assert_delivered(as_tool=LEGACY_CREATE_AGENT_CHAT_MESSAGE)


@pytest.mark.asyncio
async def test_orphan_from_prior_turn_cannot_mark_later_turn_delivered() -> None:
    """A late complete against turn N's proxy must not affect turn N+1."""
    turn_n = Turn.open()
    turn_n1 = Turn.open()

    release = asyncio.Event()
    posted = asyncio.Event()

    class SlowPost(FakeAgentTools):
        async def execute_tool_call_structured(self, tool_name, arguments):
            posted.set()
            await release.wait()
            return await super().execute_tool_call_structured(tool_name, arguments)

    turn_n.observing = ObservingTools(_inner=SlowPost(room_id=ROOM_ID))
    orphan = asyncio.create_task(turn_n.post("late"))
    await posted.wait()

    turn_n1.delivery.assert_undelivered()
    await turn_n1.observing.execute_tool_call_structured(BAND_LIST_CONTACTS, {})
    turn_n1.delivery.assert_undelivered()

    release.set()
    await orphan
    turn_n.delivery.assert_delivered()
    turn_n1.delivery.assert_undelivered()


@pytest.mark.asyncio
async def test_delivered_reaches_through_an_outer_proxy() -> None:
    class PassThrough(ToolsWrapper):
        def __init__(self, inner: object) -> None:
            self._inner = inner  # type: ignore[assignment]

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    turn = Turn.open()
    outer = PassThrough(turn.observing)

    delivery_of(outer).assert_undelivered()
    await outer.send_message("hi", mentions=["Ada"])
    assert delivery_of(outer).receipt is turn.observing.receipt


def test_plain_tools_have_no_delivery_receipt() -> None:
    delivery_of(FakeAgentTools()).assert_undelivered()
