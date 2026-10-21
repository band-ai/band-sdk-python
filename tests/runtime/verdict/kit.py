"""Scripted adapter actions and observable runtime verdict assertions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import band_sdk_core

from band.core.delivery import relay_reply
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
    AgentToolsProtocol,
    TurnResultAlreadyReported,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    HistoryProvider,
    PlatformMessage,
)
from band.runtime.delivery import DeliveryLifecycle
from band.runtime.tools import BandTool
from band.testing import MISSING_REPLY_FAILURE
from tests.runtime.helpers import (
    runtime_failures,
)

delivery_logger = DeliveryLifecycle.__module__


USER = ["@user-1"]


Step = Callable[[AgentToolsProtocol], Awaitable[Any]]


async def unknown_handle_send(tools: AgentToolsProtocol) -> None:
    try:
        await tools.send_message("hi", mentions=["@user1"])
    except ValueError:
        pass  # the model sees the error and moves on


ROWS: dict[str, tuple[list[Step], bool]] = {
    "nothing": ([], False),
    "observe-only": ([lambda t: t.get_participants()], False),
    "send-event-only": (
        [lambda t: t.send_event("thinking", message_type="thought")],
        False,
    ),
    "act": ([lambda t: t.create_chatroom()], True),
    "reply": ([lambda t: t.send_message("the answer", mentions=USER)], True),
    "reply-then-observe": (
        [
            lambda t: t.send_message("the answer", mentions=USER),
            lambda t: t.get_participants(),
        ],
        True,
    ),
    "decline": ([lambda t: t.execute_tool_call(BandTool.NO_REPLY, {})], True),
    "relay": ([lambda t: relay_reply(t, "the answer", USER)], True),
    "settled": ([lambda t: _settle(t)], True),
    "reported": (
        [lambda t: t.send_failure(band_sdk_core.AgentFailure("codex", "boom"))],
        True,
    ),
    "nightly": (
        [
            unknown_handle_send,
            lambda t: t.execute_tool_call(
                BandTool.SEND_EVENT, {"content": "retrying", "message_type": "thought"}
            ),
        ],
        False,
    ),
    "blank-send": ([lambda t: t.send_message("  ", mentions=USER)], False),
}


async def _settle(tools: AgentToolsProtocol) -> None:
    tools.turn.settle()


class ScriptedAdapter(SimpleAdapter[HistoryProvider]):
    def __init__(self, steps: list[Step], *, judges: bool = True) -> None:
        super().__init__()
        self._steps = steps
        self._judges = judges

    @property
    def judges_turns(self) -> bool:
        return self._judges

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: HistoryProvider,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        for step in self._steps:
            await step(tools)


async def crash(tools: AgentToolsProtocol) -> None:
    raise RuntimeError("provider exploded")


ADAPTER_FAILURE = ("scripted", "Provider is down")


async def report_and_stop(tools: AgentToolsProtocol) -> None:
    """An adapter's own failure path: report it, then end the turn."""
    await tools.send_failure(band_sdk_core.AgentFailure(*ADAPTER_FAILURE))
    raise TurnResultAlreadyReported(ADAPTER_FAILURE[1])


GENERIC_FAILURE = (TURN_FAILURE_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)


NATIVE_OUTCOMES = {
    "native-only": ([], False),
    "invalid-decline": ([(BandTool.NO_REPLY, {"reason": 123})], False),
    "failed-send": (
        [(BandTool.SEND_MESSAGE, {"content": "hi", "mentions": ["@missing"]})],
        False,
    ),
    "decline": ([(BandTool.NO_REPLY, {})], True),
    "reply": ([(BandTool.SEND_MESSAGE, {"content": "hi", "mentions": USER})], True),
    "act": ([(BandTool.CREATE_CHATROOM, {})], True),
    "failed-then-decline": (
        [(BandTool.NO_REPLY, {"reason": 123}), (BandTool.NO_REPLY, {})],
        True,
    ),
}


def assert_runtime_outcome(link: MagicMock, completes: bool) -> None:
    if completes:
        link.mark_processed.assert_awaited_once()
        link.mark_failed.assert_not_awaited()
        assert runtime_failures(link) == []
    else:
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()
        assert runtime_failures(link) == [MISSING_REPLY_FAILURE]
