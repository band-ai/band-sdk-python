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
from typing import Any
from unittest.mock import MagicMock

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
    Emit,
    HistoryProvider,
    PlatformMessage,
)
from band.runtime.execution import ExecutionContext
from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from band.runtime.types import SessionConfig
from band.testing import MISSING_REPLY_FAILURE
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.conftest import make_message_event
from tests.runtime.helpers import (
    ROOM_ID,
    deliver,
    failure_posts,
    run_through,
    runtime_failures,
)

execution_logger = ExecutionContext.__module__
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


@pytest.mark.parametrize("row", sorted(ROWS))
async def test_each_turn_outcome_is_reported_honestly(
    row: str, link: MagicMock
) -> None:
    steps, completes = ROWS[row]
    ctx = run_through(ScriptedAdapter(steps), link)

    await ctx._process_event(make_message_event(room_id=ROOM_ID, sender_id="user-1"))

    assert_runtime_outcome(link, completes)


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


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize("thoughts", [True, False])
@pytest.mark.parametrize("row", sorted(NATIVE_OUTCOMES))
async def test_codex_native_text_does_not_change_the_runtime_verdict(
    link: MagicMock,
    path: str,
    thoughts: bool,
    row: str,
) -> None:
    calls, completes = NATIVE_OUTCOMES[row]
    events = [
        tool_call_request(index, name, arguments)
        for index, (name, arguments) in enumerate(calls, 1)
    ]
    adapter = make_codex_adapter(
        FakeCodexClient(
            events=[*events, final_text("Closing narration"), turn_completed()]
        ),
        emit={Emit.THOUGHTS} if thoughts else set(),
    )
    await adapter.on_started("Agent", "A coding agent")
    try:
        await deliver(run_through(adapter, link), path)
    finally:
        await adapter.on_cleanup(ROOM_ID)
    assert_runtime_outcome(link, completes)


def assert_runtime_outcome(link: MagicMock, completes: bool) -> None:
    if completes:
        link.mark_processed.assert_awaited_once()
        link.mark_failed.assert_not_awaited()
        assert runtime_failures(link) == []
    else:
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()
        assert runtime_failures(link) == [MISSING_REPLY_FAILURE]


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize("thoughts", [True, False])
@pytest.mark.parametrize(
    "row",
    [
        "native-only",
        "invalid-decline",
        "decline",
        "reply",
        "act",
        "failed-then-decline",
    ],
)
async def test_acp_native_text_does_not_change_the_runtime_verdict(
    link: MagicMock,
    path: str,
    thoughts: bool,
    row: str,
) -> None:
    pytest.importorskip(
        "acp", reason="ACP extra is tested in its supported dependency lane"
    )
    from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- optional ACP extra
        ACPClientAdapter,
    )
    from tests.integrations.acp.acp_toolkit import (  # noqa: PLC0415 -- optional ACP extra
        FakeACPAgent,
        fake_agent_config,
        started_acp_adapter,
    )

    calls, completes = NATIVE_OUTCOMES[row]
    agent = FakeACPAgent()
    for index, (name, arguments) in enumerate(calls):
        if name == BandTool.NO_REPLY and arguments.get("reason") == 123:
            agent.will_call_invalid_mcp_tool(str(index), name, arguments=arguments)
        else:
            agent.will_call_mcp_tool(str(index), name, arguments=arguments)
    agent.will_say("Closing narration")
    adapter = ACPClientAdapter(
        fake_agent_config(inject_band_tools=True),
        emit={Emit.THOUGHTS} if thoughts else set(),
    )
    async with started_acp_adapter(adapter, agent):
        await deliver(run_through(adapter, link), path)
    assert_runtime_outcome(link, completes)


@pytest.mark.parametrize(
    "selection, expected",
    [
        ({"exclude_tools": {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}}, None),
        (
            {
                "include_tools": {
                    BandTool.SEND_MESSAGE,
                    BandTool.NO_REPLY,
                    BandTool.GET_PARTICIPANTS,
                }
            },
            {BandTool.SEND_MESSAGE, BandTool.NO_REPLY, BandTool.GET_PARTICIPANTS},
        ),
        (
            {
                "include_categories": {"chat"},
                "include_tools": {
                    BandTool.SEND_MESSAGE,
                    BandTool.NO_REPLY,
                    BandTool.GET_PARTICIPANTS,
                },
                "exclude_tools": {BandTool.NO_REPLY},
            },
            {BandTool.SEND_MESSAGE, BandTool.GET_PARTICIPANTS},
        ),
    ],
)
async def test_codex_advertises_filtered_central_registry_tools(
    link: MagicMock, selection: dict[str, Any], expected: set[str] | None
) -> None:
    tools = AgentTools(ROOM_ID, link.rest)
    adapter = make_codex_adapter(FakeCodexClient(), **selection)
    if expected is None:
        registry_names = {
            schema["function"]["name"]
            for schema in tools.get_openai_tool_schemas(
                capabilities=adapter.features.capabilities
            )
        }
        expected = registry_names - {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
    assert {
        schema["name"] for schema in adapter._build_dynamic_tools(tools)
    } == expected
