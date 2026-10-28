"""Codex adapter: history injection."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import pytest

from band.adapters.codex import (
    SYSTEM_CONTEXT_PREFIX,
    CodexAdapter,
    CodexAdapterConfig,
)
from band.converters.codex import CODEX_RESUME_RECORD
from band.converters.helpers import HISTORY_REPLAY_HEADER, NEW_MESSAGE_MARKER_PREFIX
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod, CodexTurnStatus
from band.runtime.formatters import CURRENT_INPUTS_HEADING
from band.runtime.tools import BandTool
from tests.adapters.codex.kit import (
    REPLAYED,
    TRANSCRIPT,
    ToolSchemaFakeTools,
    agent_input,
    answered_turn,
    make_platform_message,
    recorded_thread,
    replayed_history,
    stored,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    final_text,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.adapters.dispatch import deliver_event
from tests.roomhistory import stored as stored_text


class TestHistoryInjection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("fetch_error", "context"),
        [
            (ConnectionError("Band REST unavailable"), []),
            (
                None,
                [
                    stored_text(
                        "history without a timestamp",
                        message_id="unknown",
                        inserted_at=None,
                    )
                ],
            ),
        ],
        ids=["fetch-failed", "cutoff-unavailable"],
    )
    async def test_unavailable_history_keeps_reseed_unrecorded_until_replay_succeeds(
        self,
        started_codex: Callable[..., Awaitable[CodexAdapter]],
        caplog: pytest.LogCaptureFixture,
        fetch_error: Exception | None,
        context: list[dict[str, Any]],
    ) -> None:
        fake_client = FakeCodexClient(
            events=[
                turn_completed("turn-1", status=CodexTurnStatus.FAILED),
                tool_call_request(1, BandTool.NO_REPLY),
                turn_completed("turn-2"),
            ]
        )
        adapter = await started_codex(fake_client)
        first = agent_input("first", ToolSchemaFakeTools())
        with pytest.raises(TurnResultAlreadyReported):
            await deliver_event(adapter, first)

        tools = ToolSchemaFakeTools()
        tools.fetch_room_context_error = fetch_error
        tools.set_room_context(context)
        notice = "A contact update"
        second = replace(
            agent_input("again", tools, is_session_bootstrap=False), contacts_msg=notice
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="band.adapters.codex"):
            await deliver_event(adapter, second)

        first_turn, second_turn = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert first_turn["threadId"] != second_turn["threadId"]
        assert replayed_history(second_turn) == []
        assert tools.turn.complete
        assert CODEX_RESUME_RECORD.candidates(tools.events_sent) == {}
        assert caplog.messages == [
            "Room room-1: could not fetch history to replay into a fresh session"
        ]
        fake_client.script(*answered_turn("turn-3"))
        recovered_tools = ToolSchemaFakeTools()
        recovered = agent_input(
            "recovered", recovered_tools, is_session_bootstrap=False
        )
        recovered_tools.set_room_context(
            [stored(first.msg), stored(second.msg), stored(recovered.msg)]
        )
        await deliver_event(adapter, recovered)

        *_, reseeded = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert reseeded["threadId"] not in {
            first_turn["threadId"],
            second_turn["threadId"],
        }
        marker = reseeded["input"][-1]["text"].split("\n", 1)[0]
        assert replayed_history(reseeded) == [
            f"{HISTORY_REPLAY_HEADER.format(marker=marker)}\n[Alice]: {first.msg.content}\n[Alice]: {second.msg.content}"
        ]
        assert {
            "type": "text",
            "text": f"{SYSTEM_CONTEXT_PREFIX} {notice}",
        } in reseeded["input"]
        assert CODEX_RESUME_RECORD.candidates(recovered_tools.events_sent) == {
            recovered.room_id: reseeded["threadId"]
        }

    @pytest.mark.asyncio
    async def test_history_injected_on_resume_failure(self) -> None:
        """Resume fails, fresh thread created, first turn input contains history block."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            recorded_thread("thr-old"),
            {
                "message_type": "text",
                "content": "Can you refactor the auth module?",
                "sender_name": "Alice",
            },
            {
                "message_type": "text",
                "content": "Done — split into auth_handler.py and middleware.py",
                "sender_name": "CodexAgent",
            },
        ]

        inp = agent_input("Now add rate limiting", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        (history,) = replayed_history(turn_start)
        assert "[Alice]: Can you refactor the auth module?" in history
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history
        )
        # Task events should NOT appear in history context
        assert CODEX_RESUME_RECORD.label not in history

    @pytest.mark.asyncio
    async def test_history_not_injected_on_successful_resume(self) -> None:
        """Resume succeeds, no history injection."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            recorded_thread("thr-existing"),
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = agent_input("Continue", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        assert replayed_history(turn_start) == []

    @pytest.mark.asyncio
    async def test_history_not_injected_when_disabled(self) -> None:
        """inject_history_on_resume_failure=False: neither the bootstrap seed
        nor a later reseed replays history, and no reseed fetches it."""
        fake_client = FakeCodexClient(
            events=[
                turn_completed("turn-1", status=CodexTurnStatus.FAILED),
                tool_call_request(1, BandTool.NO_REPLY),
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(inject_history_on_resume_failure=False),
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        raw_history = [stored(make_platform_message(content="Hello"))]
        seeding_input = agent_input(
            "Continue", ToolSchemaFakeTools(), raw_history=raw_history
        )
        later_tools = ToolSchemaFakeTools()
        reseeding_input = agent_input("Again", later_tools, is_session_bootstrap=False)
        later_tools.set_room_context(
            [*raw_history, stored(seeding_input.msg), stored(reseeding_input.msg)]
        )

        with pytest.raises(TurnResultAlreadyReported):
            await deliver_event(adapter, seeding_input)
        await deliver_event(adapter, reseeding_input)

        seeding, reseeding = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert reseeding["threadId"] != seeding["threadId"]
        assert replayed_history(seeding) == replayed_history(reseeding) == []
        assert later_tools.context_calls == []

    @pytest.mark.asyncio
    async def test_history_filters_non_text_messages(self) -> None:
        """Only canonical text messages appear in injected context."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {**recorded_thread("thr-old"), "sender_name": "System"},
            {
                "message_type": "tool_call",
                "content": '{"name": "foo"}',
                "sender_name": "Agent",
            },
            {
                "message_type": "tool_result",
                "content": "result",
                "sender_name": "Agent",
            },
            {
                "message_type": "thought",
                "content": "thinking...",
                "sender_name": "Agent",
            },
            {"message_type": "error", "content": "oops", "sender_name": "Agent"},
            {
                "message_type": "text",
                "content": "Hello world",
                "sender_name": "Alice",
            },
            {
                "message_type": "message",
                "content": "Hi there",
                "sender_name": "Bob",
            },
        ]

        inp = agent_input("Go", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        (text,) = replayed_history(turn_start)
        assert "[Alice]: Hello world" in text
        # "message" is not a MessageType value; nothing on the platform
        # produces it, so it does not survive replay.
        assert "[Bob]: Hi there" not in text
        assert CODEX_RESUME_RECORD.label not in text
        assert "thinking..." not in text
        assert "oops" not in text
        assert "tool_call" not in text

    @pytest.mark.asyncio
    async def test_history_respects_max_messages(self) -> None:
        """Only last max_history_messages are injected."""
        events = [
            tool_call_request(1, BandTool.NO_REPLY),
            final_text("Done."),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(max_history_messages=3)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history: list[dict[str, Any]] = [
            recorded_thread("thr-old"),
        ]
        messages = [f"Message {i}" for i in range(10)]
        raw_history.extend(
            {
                "message_type": "text",
                "content": content,
                "sender_name": "Alice",
            }
            for content in messages
        )

        inp = agent_input("Go", tools, raw_history=raw_history)
        await deliver_event(adapter, inp)

        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        (text,) = replayed_history(turn_start)
        marker = turn_start["input"][-1]["text"].splitlines()[0]
        expected = "\n".join(f"[Alice]: {content}" for content in messages[-3:])
        assert text == f"{HISTORY_REPLAY_HEADER.format(marker=marker)}\n{expected}"

    @pytest.mark.asyncio
    async def test_history_is_replayed_only_into_the_seeding_turn(self) -> None:
        """Once the seeding turn completes, later turns carry no transcript."""
        fake_client = FakeCodexClient(
            events=[
                tool_call_request(1, BandTool.NO_REPLY),
                turn_completed("turn-1"),
                tool_call_request(2, BandTool.NO_REPLY),
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Codex Agent", "A coding agent")
        raw_history = [stored(make_platform_message(content="Hello"))]
        seeding_input = agent_input(
            "Go", ToolSchemaFakeTools(), raw_history=raw_history
        )
        later_tools = ToolSchemaFakeTools()
        later_input = agent_input("Again", later_tools, is_session_bootstrap=False)
        # The room's stored transcript stays fetchable after the seed.
        later_tools.set_room_context(
            [*raw_history, stored(seeding_input.msg), stored(later_input.msg)]
        )

        await deliver_event(adapter, seeding_input)
        await deliver_event(adapter, later_input)

        seeding, later = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert "[Alice]: Hello" in str(seeding["input"])
        assert replayed_history(later) == []

    @pytest.mark.asyncio
    async def test_the_replay_is_framed_and_the_marker_opens_the_current_inputs(
        self,
    ) -> None:
        """The replay uses the shared read-only header, and the current inputs
        come last, under the marker that header names."""
        fake_client = FakeCodexClient(events=answered_turn())
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Codex Agent", "A coding agent")

        await deliver_event(
            adapter, agent_input("Go", ToolSchemaFakeTools(), raw_history=TRANSCRIPT)
        )

        (turn,) = fake_client.params_of(CodexRequestMethod.TURN_START)
        *_, replay, current = [item["text"] for item in turn["input"]]
        marker, current_inputs = current.split("\n", 1)
        assert marker.startswith(NEW_MESSAGE_MARKER_PREFIX)
        assert replay == HISTORY_REPLAY_HEADER.format(marker=marker) + "\n" + REPLAYED
        assert current_inputs.startswith(CURRENT_INPUTS_HEADING)
