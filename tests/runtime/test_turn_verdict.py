"""The turn verdict through the real runtime: ExecutionContext -> preprocessor ->
``SimpleAdapter.on_event`` -> real ``AgentTools``.

One row per band-sdk-core turn-outcome fixture, plus the nightly loss (a send to
an unknown handle, then narration). A complete turn is marked PROCESSED with no
report; a missing reply is reported once (provider ``band-runtime``, core's
text) and marked FAILED.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import band_sdk_core
import pytest

from band.core.delivery import relay_reply
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
    AgentToolsProtocol,
    TurnResultAlreadyReported,
)
from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
    SYNTHETIC_SENDER_TYPE,
    HistoryProvider,
    PlatformMessage,
)
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.tools import BandTool
from band.runtime.types import SessionConfig
from band.testing import MISSING_REPLY_FAILURE
from tests.conftest import make_message_event, make_participant_mock

AGENT_ID = "agent-123"
execution_logger = ExecutionContext.__module__
ROOM_ID = "room-123"
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


@pytest.fixture
def link(mock_rest_client: MagicMock) -> MagicMock:
    link = MagicMock()
    link.agent_id = AGENT_ID
    link.rest = mock_rest_client
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(
            data=[make_participant_mock("user-1", "User One", "User")]
        )
    )
    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    return link


def run_through(
    adapter: SimpleAdapter[Any],
    link: MagicMock,
    *,
    config: SessionConfig | None = None,
) -> ExecutionContext:
    preprocessor = DefaultPreprocessor()

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        inp = await preprocessor.process(ctx=ctx, event=event, agent_id=AGENT_ID)
        if inp is not None:
            await adapter.on_event(inp)

    return ExecutionContext(
        ROOM_ID,
        link,
        handler,
        config=config or SessionConfig(enable_context_hydration=False),
        agent_id=AGENT_ID,
    )


async def deliver(ctx: ExecutionContext, path: str) -> None:
    """Deliver one user message live over the socket, or from the backlog."""
    match path:
        case "live":
            await ctx._process_event(
                make_message_event(room_id=ROOM_ID, sender_id="user-1")
            )
        case "backlog":
            await ctx._process_backlog_message(
                PlatformMessage(
                    id="msg-backlog",
                    room_id=ROOM_ID,
                    content="@agent hi",
                    sender_id="user-1",
                    sender_type="User",
                    sender_name="User One",
                    message_type="text",
                    metadata={},
                    created_at=datetime.now(UTC),
                )
            )


async def crash(tools: AgentToolsProtocol) -> None:
    raise RuntimeError("provider exploded")


ADAPTER_FAILURE = ("scripted", "Provider is down")


async def report_and_stop(tools: AgentToolsProtocol) -> None:
    """An adapter's own failure path: report it, then end the turn."""
    await tools.send_failure(band_sdk_core.AgentFailure(*ADAPTER_FAILURE))
    raise TurnResultAlreadyReported(ADAPTER_FAILURE[1])


def failure_posts(link: MagicMock) -> list[tuple[str, str]]:
    """Every attempted failure post, as (provider, text), in order."""
    return [
        (
            call.kwargs["event"].metadata["failure"]["provider"],
            call.kwargs["event"].content,
        )
        for call in link.rest.agent_api_events.create_agent_chat_event.call_args_list
        if call.kwargs["event"].metadata and "failure" in call.kwargs["event"].metadata
    ]


def runtime_failures(link: MagicMock) -> list[tuple[str, str]]:
    return [post for post in failure_posts(link) if post[0] == TURN_FAILURE_PROVIDER]


@pytest.mark.parametrize("row", sorted(ROWS))
async def test_each_turn_outcome_is_reported_honestly(
    row: str, link: MagicMock
) -> None:
    steps, completes = ROWS[row]
    ctx = run_through(ScriptedAdapter(steps), link)

    await ctx._process_event(make_message_event(room_id=ROOM_ID, sender_id="user-1"))

    if completes:
        link.mark_processed.assert_awaited_once()
        link.mark_failed.assert_not_awaited()
        assert runtime_failures(link) == []
    else:
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()
        assert runtime_failures(link) == [MISSING_REPLY_FAILURE]


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize(
    ("steps", "level", "traceback"),
    [
        pytest.param([], logging.DEBUG, False, id="missing-reply"),
        pytest.param([crash], logging.ERROR, True, id="crash"),
    ],
)
async def test_only_an_unreported_failure_is_logged_as_a_runtime_error(
    link: MagicMock,
    caplog: pytest.LogCaptureFixture,
    path: str,
    steps: list[Step],
    level: int,
    traceback: bool,
) -> None:
    """A missing reply was logged as a WARNING where it was reported, so the
    runtime keeps it out of ERROR alerting; a crash still alerts with its
    traceback."""
    ctx = run_through(ScriptedAdapter(steps), link)

    with caplog.at_level(logging.DEBUG, logger=execution_logger):
        await deliver(ctx, path)

    (record,) = [r for r in caplog.records if r.message.startswith("Error processing")]
    assert record.levelno == level
    assert bool(record.exc_info) is traceback


GENERIC_FAILURE = (TURN_FAILURE_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize("report_posts", [True, False], ids=["posted", "post-failed"])
@pytest.mark.parametrize(
    ("steps", "report"),
    [
        pytest.param([], MISSING_REPLY_FAILURE, id="missing-reply"),
        pytest.param([report_and_stop], ADAPTER_FAILURE, id="adapter-report"),
    ],
)
async def test_the_runtime_reports_a_turn_only_when_its_report_did_not_post(
    link: MagicMock,
    path: str,
    steps: list[Step],
    report: tuple[str, str],
    report_posts: bool,
) -> None:
    """Raising ``TurnResultAlreadyReported`` is not enough: the runtime's
    fallback runs unless the turn's own report actually reached the room."""
    if not report_posts:
        create = link.rest.agent_api_events.create_agent_chat_event
        create.side_effect = [RuntimeError("transient 503"), create.return_value]
    ctx = run_through(ScriptedAdapter(steps), link)

    await deliver(ctx, path)

    link.mark_failed.assert_awaited_once()
    fallback = [] if report_posts else [GENERIC_FAILURE]
    assert failure_posts(link) == [report, *fallback]


async def test_a_session_that_reports_no_failures_posts_no_missing_reply(
    link: MagicMock,
) -> None:
    config = SessionConfig(
        enable_context_hydration=False, report_turn_failures_to_room=False
    )
    ctx = run_through(ScriptedAdapter([]), link, config=config)

    await deliver(ctx, "live")

    link.mark_failed.assert_awaited_once()
    assert runtime_failures(link) == []


async def test_an_exempt_adapter_is_never_judged(link: MagicMock) -> None:
    ctx = run_through(ScriptedAdapter([], judges=False), link)

    await ctx._process_event(make_message_event(room_id=ROOM_ID, sender_id="user-1"))

    link.mark_processed.assert_awaited_once()
    assert runtime_failures(link) == []


async def test_a_contact_hub_turn_is_never_judged(link: MagicMock) -> None:
    ctx = run_through(ScriptedAdapter([]), link)

    await ctx._process_event(
        make_message_event(
            room_id=ROOM_ID,
            sender_id=SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
            sender_type=SYNTHETIC_SENDER_TYPE,
        )
    )

    assert runtime_failures(link) == []
    link.mark_failed.assert_not_awaited()
