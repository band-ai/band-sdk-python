"""Tests for band.runtime.oneshot.OneShotInvoker.

OneShotInvoker is the request/response counterpart to Agent: one forwarded
bridge event in, one adapter execution out, no per-room state across calls.
These tests exercise the public path (startup → handle_event) plus the
module-level pure helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import ToolUseBlock
from band_rest import (
    CreateAgentChatMessageResponse,
    GetAgentChatContextResponse,
    GetAgentChatContextResponseMetadata,
    MessageSentResponse,
)
from pydantic import BaseModel, Field

from band.adapters.anthropic import AnthropicAdapter
from band.runtime.formatters import build_participants_message
from band.runtime.oneshot import (
    OneShotEnvelopeError,
    OneShotInvoker,
    _build_platform_message,
    _lookup_sender_name,
    _parse_inserted_at,
)
from tests.runtime.conftest import ctx_item, make_link_mock, platform_msg


# ---------------------------------------------------------------------------
# Helpers local to this file. The BandLink/REST fakes (make_link_mock,
# platform_msg, ctx_item) live in tests/runtime/conftest.py — shared with
# other tests/runtime files, real band_rest model instances rather than bare
# MagicMocks.
# ---------------------------------------------------------------------------


def _make_adapter_mock() -> MagicMock:
    """A fake adapter that records the AgentInput it was run with as a plain
    ``received_input`` attribute — reading that is what a test should assert
    on, rather than reaching into ``on_event.call_args.args[0]``. Still an
    AsyncMock underneath, so ``on_event.assert_awaited_once()`` etc. keep
    working unchanged.
    """
    adapter = MagicMock()
    adapter.received_input = None

    async def _capture(inp: Any) -> None:
        adapter.received_input = inp

    adapter.on_started = AsyncMock()
    adapter.on_event = AsyncMock(side_effect=_capture)
    adapter.on_cleanup = AsyncMock()
    return adapter


async def _make_invoker(
    link: MagicMock,
    adapter: MagicMock | AnthropicAdapter | None = None,
    *,
    agent_id: str = "agent-1",
    drain_cap: int = 50,
    history_page_cap: int = 20,
) -> OneShotInvoker:
    invoker = OneShotInvoker(
        link=link,
        adapter=adapter or _make_adapter_mock(),
        agent_id=agent_id,
        drain_cap=drain_cap,
        history_page_cap=history_page_cap,
    )
    await invoker.startup()
    return invoker


def _msg_body(
    *,
    msg_id: str = "msg-1",
    room_id: str = "room-1",
    sender_id: str = "user-1",
    sender_type: str = "User",
    content: str = "@bot hello",
    agent_id: str = "agent-1",
) -> dict[str, Any]:
    return {
        "event_type": "message_created",
        "agent_id": agent_id,
        "room_id": room_id,
        "payload": {
            "id": msg_id,
            "content": content,
            "sender_id": sender_id,
            "sender_type": sender_type,
            "message_type": "user",
            "inserted_at": "2026-05-21T10:00:00Z",
        },
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseInsertedAt:
    def test_parses_iso_z(self) -> None:
        dt = _parse_inserted_at("2026-05-21T10:00:00Z")
        assert dt.year == 2026 and dt.month == 5 and dt.day == 21
        assert dt.tzinfo is not None

    def test_parses_iso_offset(self) -> None:
        dt = _parse_inserted_at("2026-05-21T10:00:00+00:00")
        assert dt.tzinfo is not None

    def test_falls_back_to_now_on_invalid(self) -> None:
        before = datetime.now(timezone.utc)
        dt = _parse_inserted_at("not a date")
        after = datetime.now(timezone.utc)
        assert before <= dt <= after

    def test_falls_back_to_now_on_none(self) -> None:
        dt = _parse_inserted_at(None)
        assert dt.tzinfo is not None


class TestLookupSenderName:
    def test_finds_by_id(self) -> None:
        participants = [
            {"id": "u1", "name": "Alice"},
            {"id": "u2", "name": "Bob"},
        ]
        assert _lookup_sender_name(participants, "u2") == "Bob"

    def test_returns_none_when_not_found(self) -> None:
        assert _lookup_sender_name([{"id": "x", "name": "x"}], "y") is None

    def test_returns_none_for_empty_sender_id(self) -> None:
        assert _lookup_sender_name([{"id": "x"}], None) is None
        assert _lookup_sender_name([{"id": "x"}], "") is None


class TestBuildPlatformMessage:
    def test_basic(self) -> None:
        payload = {
            "id": "m1",
            "content": "hello",
            "sender_id": "u1",
            "sender_type": "User",
            "message_type": "user",
            "inserted_at": "2026-05-21T10:00:00Z",
        }
        msg = _build_platform_message(payload, "r1", "Alice", [])
        assert msg.id == "m1"
        assert msg.room_id == "r1"
        assert msg.content == "hello"
        assert msg.sender_id == "u1"
        assert msg.sender_type == "User"
        assert msg.sender_name == "Alice"
        assert msg.message_type == "user"
        assert msg.created_at.year == 2026

    def test_defaults_for_missing_fields(self) -> None:
        msg = _build_platform_message({"id": "m1"}, "r1", None, [])
        assert msg.content == ""
        assert msg.sender_id == ""
        assert msg.sender_type == "User"
        assert msg.message_type == "user"

    def test_translates_uuid_mentions_via_participants(self) -> None:
        # Mirrors DefaultPreprocessor: the platform normalizes mentions to
        # @[[uuid]]; the turn the LLM sees must carry @handle instead.
        payload = {"id": "m1", "content": "@[[agent-9]] remember this about me"}
        participants = [{"id": "agent-9", "name": "Bot", "handle": "org/bot"}]
        msg = _build_platform_message(payload, "r1", "Alice", participants)
        assert msg.content == "@org/bot remember this about me"


# ---------------------------------------------------------------------------
# Startup / lifecycle
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_fetches_metadata_and_primes_adapter(self) -> None:
        link = make_link_mock(agent_name="Weather", agent_description="forecasts")
        adapter = _make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()

        assert invoker.agent_name == "Weather"
        assert invoker.agent_description == "forecasts"
        # Adapter primed with identity + metadata.
        assert getattr(adapter, "_band_agent_id") == "agent-1"
        adapter.on_started.assert_awaited_once_with("Weather", "forecasts")

    async def test_startup_is_idempotent(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()
        await invoker.startup()

        link.rest.agent_api_identity.get_agent_me.assert_awaited_once()
        adapter.on_started.assert_awaited_once()

    async def test_handle_event_before_startup_raises(self) -> None:
        link = make_link_mock()
        invoker = OneShotInvoker(
            link=link, adapter=_make_adapter_mock(), agent_id="agent-1"
        )
        with pytest.raises(RuntimeError, match="startup"):
            await invoker.handle_event(_msg_body())

    async def test_shutdown_disconnects_link(self) -> None:
        link = make_link_mock()
        invoker = await _make_invoker(link)
        await invoker.shutdown()
        link.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_event routing
# ---------------------------------------------------------------------------


class TestHandleEventRouting:
    async def test_non_message_event_returns_ignored(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(
            {"event_type": "room_added", "room_id": "r1", "payload": {}}
        )
        assert result["status"] == "ignored"
        assert result["event_type"] == "room_added"
        adapter.on_event.assert_not_awaited()
        link.get_next_message.assert_not_awaited()

    async def test_room_removed_triggers_adapter_cleanup(self) -> None:
        """Regression: long-running containers keep one adapter alive across
        many rooms. Without on_cleanup on room teardown, per-room caches
        (Anthropic history, Claude SDK sessions, etc.) leak.
        """
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(
            {"event_type": "room_removed", "room_id": "r1", "payload": {}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r1")
        assert result == {
            "status": "cleaned_up",
            "event_type": "room_removed",
            "room_id": "r1",
        }
        link.get_next_message.assert_not_awaited()

    async def test_room_deleted_triggers_adapter_cleanup(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        await invoker.handle_event(
            {"event_type": "room_deleted", "room_id": "r1", "payload": {}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r1")

    async def test_room_removed_falls_back_to_payload_id(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        await invoker.handle_event(
            {"event_type": "room_removed", "payload": {"id": "r-payload"}}
        )

        adapter.on_cleanup.assert_awaited_once_with("r-payload")

    async def test_room_removed_swallows_cleanup_errors(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        adapter.on_cleanup = AsyncMock(side_effect=RuntimeError("adapter blew up"))
        invoker = await _make_invoker(link, adapter)

        # Must not raise — a flaky adapter cleanup can't kill the container.
        result = await invoker.handle_event(
            {"event_type": "room_removed", "room_id": "r1", "payload": {}}
        )
        assert result["status"] == "cleaned_up"

    async def test_missing_room_id_raises_envelope_error(self) -> None:
        link = make_link_mock()
        invoker = await _make_invoker(link)
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "payload": {"id": "msg-1", "sender_id": "u", "content": "x"},
        }
        with pytest.raises(OneShotEnvelopeError, match="room_id"):
            await invoker.handle_event(body)

    async def test_missing_message_id_raises_envelope_error(self) -> None:
        link = make_link_mock()
        invoker = await _make_invoker(link)
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "room_id": "room-1",
            "payload": {"sender_id": "u", "content": "x"},
        }
        with pytest.raises(OneShotEnvelopeError):
            await invoker.handle_event(body)

    async def test_falls_back_to_payload_chat_room_id(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        invoker = await _make_invoker(link)
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "payload": {
                "id": "msg-1",
                "chat_room_id": "fallback-room",
                "sender_id": "u",
                "sender_type": "User",
                "content": "hi",
                "inserted_at": "2026-05-21T10:00:00Z",
            },
        }
        result = await invoker.handle_event(body)
        assert result["room_id"] == "fallback-room"


# ---------------------------------------------------------------------------
# Message processing lifecycle
# ---------------------------------------------------------------------------


class TestProcessMessage:
    async def test_processes_message(self) -> None:
        link = make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        assert result["status"] == "done"
        assert result["room_id"] == "room-1"
        assert result["message_id"] == "msg-1"

        adapter.on_event.assert_awaited_once()
        inp = adapter.received_input
        assert inp.msg.id == "msg-1"
        assert inp.msg.sender_name == "Alice"

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

    async def test_skips_self_message(self) -> None:
        link = make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)
        body = _msg_body(
            msg_id="msg-self",
            sender_id="agent-1",
            sender_type="Agent",
            content="echo",
        )

        result = await invoker.handle_event(body)
        assert result["status"] == "skipped_self"
        adapter.on_event.assert_not_awaited()
        link.get_next_message.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_no_pending(self) -> None:
        """get_next_message returns None — already processed by a sibling."""
        link = make_link_mock(next_messages=[None])
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        assert result["status"] == "no_pending"
        assert result["message_id"] == "msg-1"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_different_message_is_next(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-other")])
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        assert result["status"] == "already_processed"
        assert result["message_id"] == "msg-1"
        assert result["next_open"] == "msg-other"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_claim_propagates_get_next_message_failure(self) -> None:
        """Regression: a transient ``/next`` failure at the claim step must
        not be silently treated as ``no_pending`` — that would leave the
        message open on the platform and tell the bridge it was handled.
        """
        link = make_link_mock()
        link.get_next_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="network down"):
            await invoker.handle_event(_msg_body(msg_id="msg-1"))

        # No claim attempted, no adapter run — caller can retry.
        link.mark_processing.assert_not_awaited()
        adapter.on_event.assert_not_awaited()

    async def test_marks_failed_on_adapter_error(self) -> None:
        link = make_link_mock(next_messages=[platform_msg("msg-1")])
        adapter = _make_adapter_mock()
        adapter.on_event = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        invoker = await _make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="LLM crashed"):
            await invoker.handle_event(_msg_body())

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()


# ---------------------------------------------------------------------------
# Participant roster surfaced to the model
#
# The long-running path treats "no prior roster sent yet" as changed (see
# ExecutionContext.participants_changed: `_last_participants_sent is None`
# -> True), so a room's very first message already carries the roster via
# `participants_msg`. OneShotInvoker has no cross-call state to diff
# against, so every invocation is that same "first time" case — it should
# always build and pass the current roster, never a hardcoded None.
# ---------------------------------------------------------------------------


class TestParticipantsSurfaced:
    async def test_participants_msg_reflects_current_roster(self) -> None:
        participants = [
            {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            {"id": "agent-2", "name": "Bot2", "type": "Agent", "handle": "bot2"},
        ]
        link = make_link_mock(
            participants=participants,
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        await invoker.handle_event(_msg_body())

        adapter.on_event.assert_awaited_once()
        inp = adapter.received_input
        assert inp.participants_msg == build_participants_message(participants)

    async def test_participants_msg_none_when_room_has_no_other_participants(
        self,
    ) -> None:
        """Even an empty roster is a deliberate 'no one else here' statement,
        not an absent one — the model shouldn't have to call a tool to learn
        it's alone in the room.
        """
        link = make_link_mock(
            participants=[],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        await invoker.handle_event(_msg_body())

        inp = adapter.received_input
        assert inp.participants_msg == build_participants_message([])


# ---------------------------------------------------------------------------
# History pagination
#
# The real context endpoint paginates with a cursor (``next_cursor`` /
# ``has_more``); ``page``/``page_size`` are deprecated and scheduled for
# removal. A room whose history spans more than one page must not silently
# lose the earlier pages.
# ---------------------------------------------------------------------------


class TestHistoryPagination:
    async def test_long_running_room_keeps_facts_from_the_start_of_the_conversation(
        self,
    ) -> None:
        """A project channel active long enough to span more than one fetch
        holds facts the model still needs — the codename set on day one is as
        relevant as this morning's standup update. Neither should quietly
        fall out of context just because the room outgrew a single page.
        """
        link = make_link_mock(
            history_pages=[
                [ctx_item("hist-1", content="the project codename is NIGHTHAWK")],
                [ctx_item("hist-2", content="today's standup moved to 3pm")],
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert "the project codename is NIGHTHAWK" in history_content
        assert "today's standup moved to 3pm" in history_content
        assert "history_truncated" not in result

    async def test_room_active_for_months_gets_a_prompt_reply_not_a_full_replay(
        self,
    ) -> None:
        """A room that's been running for months can carry far more history
        than the retention window holds. The agent still has to answer
        promptly, so it keeps only the trailing pages rather than replaying
        the entire backlog on every message — and says so honestly instead
        of presenting a partial conversation as the whole thing.

        Critically, "partial" must mean the *most recent* days, not the
        oldest ones — the endpoint is oldest-first with no way to request
        newest-first, so pagination has to walk to the true end and evict
        old pages, never stop early and keep the stale ones.
        """
        months_of_daily_updates = [
            [ctx_item(f"hist-{day}", content=f"day {day} update")] for day in range(5)
        ]
        link = make_link_mock(
            history_pages=months_of_daily_updates,
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter, history_page_cap=3)

        result = await invoker.handle_event(_msg_body())

        get_context = link.rest.agent_api_context.get_agent_chat_context
        assert get_context.await_count == 5  # walked to the true end, not the cap

        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert history_content == [
            "day 2 update",
            "day 3 update",
            "day 4 update",
        ]
        assert result["history_truncated"] is True

    async def test_first_page_fetch_failure_is_reported_as_truncated(self) -> None:
        """A page-0 fetch failure is the worst case of "history incomplete" —
        the LLM runs with zero history instead of some. It must not be
        reported identically to a fully successful fetch.
        """
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=RuntimeError("context endpoint unavailable")
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        assert adapter.received_input.history.raw == []
        assert result["history_truncated"] is True

    async def test_has_more_without_a_next_cursor_stops_instead_of_looping(
        self,
    ) -> None:
        """``has_more=True`` with no ``next_cursor`` is a backend contract
        violation the response type doesn't rule out (``next_cursor`` is
        ``Optional``). Re-requesting with ``cursor=None`` would just refetch
        page 0 forever (until the page cap) and duplicate its items — this
        must instead stop after one page and say so honestly.
        """
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        malformed_response = GetAgentChatContextResponse(
            data=[ctx_item("hist-1", content="page with a broken cursor contract")],
            metadata=GetAgentChatContextResponseMetadata(
                has_more=True,
                limit=50,
                next_cursor=None,
            ),
        )
        get_context = AsyncMock(return_value=malformed_response)
        link.rest.agent_api_context.get_agent_chat_context = get_context
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter, history_page_cap=5)

        result = await invoker.handle_event(_msg_body())

        get_context.assert_awaited_once()
        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert history_content.count("page with a broken cursor contract") == 1
        assert result["history_truncated"] is True


# ---------------------------------------------------------------------------
# Drain (race fix + self-skip + cap surfacing)
# ---------------------------------------------------------------------------


class TestDrain:
    async def test_drains_messages_seen_by_llm(self) -> None:
        """The case drain is for: the LLM saw msg-2 and msg-3 in its history
        snapshot. Drain marks them processed without re-invoking the LLM.
        """
        link = make_link_mock(
            history_items=[ctx_item("msg-2"), ctx_item("msg-3")],
            next_messages=[
                platform_msg("msg-1"),  # claim check
                platform_msg("msg-2"),  # drain (in snapshot)
                platform_msg("msg-3"),  # drain (in snapshot)
                None,
            ],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        assert result["status"] == "done"
        assert result["drained"] == ["msg-2", "msg-3"]
        adapter.on_event.assert_awaited_once()  # LLM ran exactly once
        processed = [c.args for c in link.mark_processed.await_args_list]
        assert processed == [
            ("room-1", "msg-1"),
            ("room-1", "msg-2"),
            ("room-1", "msg-3"),
        ]

    async def test_drain_leaves_messages_not_in_snapshot_open(self) -> None:
        """Drain race fix: msg-2 arrived after the history snapshot (it's not
        in seen_ids). Drain must stop and leave it open for the next
        invocation rather than swallowing it without an LLM call.
        """
        link = make_link_mock(
            history_items=[ctx_item("msg-1")],  # snapshot = msg-1 only
            next_messages=[
                platform_msg("msg-1"),  # claim check
                platform_msg("msg-2"),  # arrived after snapshot → leave open
                None,
            ],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        processed_ids = [c.args[1] for c in link.mark_processed.await_args_list]
        assert processed_ids == ["msg-1"], (
            f"drain must not swallow msg-2; got mark_processed={processed_ids}"
        )
        assert "msg-2" not in result.get("drained", [])

    async def test_drain_skips_self_messages_defensively(self) -> None:
        """If the platform returns one of our own messages from
        get_next_message during drain, skip it without marking — parity with
        the SDK's ExecutionContext self-message guard.
        """
        self_msg = platform_msg("msg-self", sender_type="Agent", sender_id="agent-1")
        link = make_link_mock(
            history_items=[ctx_item("msg-1"), ctx_item("msg-self")],
            next_messages=[
                platform_msg("msg-1"),  # claim check
                self_msg,  # our own message — skip
                None,
            ],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        processed_ids = [c.args[1] for c in link.mark_processed.await_args_list]
        assert "msg-self" not in processed_ids, (
            f"drain must skip self-messages; got mark_processed={processed_ids}"
        )
        assert "msg-self" not in result.get("drained", [])

    async def test_drain_truncated_surfaced(self) -> None:
        """When the drain cap fires, the response carries drain_truncated so
        the bridge gets a signal.
        """
        # Always return an in-snapshot message so drain never naturally stops;
        # the cap is the only exit.
        always_stale = platform_msg("msg-x")
        link = make_link_mock(history_items=[ctx_item("msg-x")])
        link.get_next_message = AsyncMock(
            side_effect=[platform_msg("msg-1")]  # claim check
            + [always_stale] * 10  # drain keeps finding msg-x
        )
        invoker = await _make_invoker(link, _make_adapter_mock(), drain_cap=3)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        assert result["status"] == "done"
        assert result.get("drain_truncated") is True


# ---------------------------------------------------------------------------
# Custom tool dispatch through a real adapter and its real AgentTools.
# ---------------------------------------------------------------------------


class VaultCodeInput(BaseModel):
    """Look up the vault code for a topic."""

    topic: str = Field(description="Topic to look up")


class TestCustomToolDispatch:
    async def test_custom_tool_result_reaches_the_platform_reply(self) -> None:
        """The model calls a custom tool it needs to answer, then reports
        back via the real band_send_message platform tool.
        """
        looked_up: list[str] = []

        async def lookup_vault_code(args: VaultCodeInput) -> str:
            looked_up.append(args.topic)
            return "vault code is 4471-ECHO"

        link = make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        link.rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            return_value=CreateAgentChatMessageResponse(
                data=MessageSentResponse(id="sent-1", recipients=[], success=True)
            )
        )

        adapter = AnthropicAdapter(
            additional_tools=[(VaultCodeInput, lookup_vault_code)]
        )
        invoker = await _make_invoker(link, adapter)

        turn_1 = MagicMock(stop_reason="tool_use")
        turn_1.content = [
            ToolUseBlock(
                type="tool_use", id="call-1", name="vaultcode", input={"topic": "vault"}
            )
        ]
        turn_2 = MagicMock(stop_reason="tool_use")
        turn_2.content = [
            ToolUseBlock(
                type="tool_use",
                id="call-2",
                name="band_send_message",
                input={
                    "content": "The vault code is 4471-ECHO",
                    "mentions": ["alice"],
                },
            )
        ]
        turn_3 = MagicMock(stop_reason="end_turn")
        turn_3.content = []

        with patch.object(
            adapter, "_call_anthropic", AsyncMock(side_effect=[turn_1, turn_2, turn_3])
        ):
            result = await invoker.handle_event(_msg_body())

        assert result["status"] == "done"
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

        # Ran directly, not via AgentTools.execute_tool_call.
        assert looked_up == ["vault"]

        link.rest.agent_api_messages.create_agent_chat_message.assert_awaited_once()
        sent = link.rest.agent_api_messages.create_agent_chat_message.call_args.kwargs[
            "message"
        ]
        assert "4471-ECHO" in sent.content
        assert sent.mentions[0].id == "user-1"


# ---------------------------------------------------------------------------
# Tool-call replay across a container crash.
#
# OneShotInvoker keeps no state across calls by design, so nothing survives
# a crash between a tool's side effect and mark_processed — the platform's
# /next re-serves the same still-"processing" message to whatever container
# picks it up next, and a fresh adapter instance replays the whole turn from
# scratch. This documents that as current, real behavior (not a bug this
# file can fix — see band.runtime.single_instance and
# band.runtime.claims.MessageClaimRegistry, both of which state the same
# cross-process boundary is out of the SDK's reach).
# ---------------------------------------------------------------------------


class SendEmailInput(BaseModel):
    """Send an email to an address."""

    to: str


class TestToolCallReplayAcrossCrash:
    async def test_crash_before_mark_processed_replays_the_tool(self) -> None:
        sent_emails: list[str] = []

        async def send_email(args: SendEmailInput) -> str:
            sent_emails.append(args.to)
            return "sent"

        link = make_link_mock(next_messages=[platform_msg("msg-1")])

        # --- Attempt 1: the tool's side effect fires, then the container
        # dies before the turn finishes (never reaches mark_processed).
        adapter_a = AnthropicAdapter(additional_tools=[(SendEmailInput, send_email)])
        invoker_a = await _make_invoker(link, adapter_a)

        tool_use_email = MagicMock(stop_reason="tool_use")
        tool_use_email.content = [
            ToolUseBlock(
                type="tool_use",
                id="call-1",
                name="sendemail",
                input={"to": "boss@example.com"},
            )
        ]
        with (
            patch.object(
                adapter_a,
                "_call_anthropic",
                AsyncMock(side_effect=[tool_use_email, SystemExit("container killed")]),
            ),
            pytest.raises(SystemExit),
        ):
            await invoker_a.handle_event(_msg_body())

        assert sent_emails == ["boss@example.com"]
        link.mark_processed.assert_not_awaited()
        link.mark_failed.assert_not_awaited()

        # --- Restart: a fresh container, fresh adapter, same still-open
        # message — /next re-serves it exactly as it would after a crash.
        link.get_next_message = AsyncMock(side_effect=[platform_msg("msg-1"), None])
        link.mark_processing.reset_mock()
        adapter_b = AnthropicAdapter(additional_tools=[(SendEmailInput, send_email)])
        invoker_b = await _make_invoker(link, adapter_b)

        turn_end = MagicMock(stop_reason="end_turn")
        turn_end.content = []
        with patch.object(
            adapter_b,
            "_call_anthropic",
            AsyncMock(side_effect=[tool_use_email, turn_end]),
        ):
            result = await invoker_b.handle_event(_msg_body())

        assert result["status"] == "done"
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")

        # The email tool fired twice for one logical request — nothing in
        # OneShotInvoker (or the platform's claim primitives it relies on)
        # prevents a crash-and-restart from replaying a completed side
        # effect. Custom tools with real-world side effects need their own
        # idempotency (e.g. keying on tool arguments), same as any at-least-
        # once delivery system.
        assert sent_emails == ["boss@example.com", "boss@example.com"]
