"""Tests for thenvoi.runtime.oneshot.OneShotInvoker.

OneShotInvoker is the request/response counterpart to Agent: one forwarded
bridge event in, one adapter execution out, no per-room state across calls.
These tests exercise the public path (startup → handle_event) plus the
module-level pure helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.oneshot import (
    OneShotEnvelopeError,
    OneShotInvoker,
    _build_platform_message,
    _lookup_sender_name,
    _parse_inserted_at,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_participant_mock(p: dict[str, Any]) -> MagicMock:
    """Build a participant mock. ``name`` must be set after construction —
    passing ``name=`` to MagicMock() sets the mock's identity, not its
    ``.name`` attribute.
    """
    mock = MagicMock(id=p["id"], type=p["type"], handle=p.get("handle"))
    mock.name = p["name"]
    return mock


def _platform_msg(
    msg_id: str, *, sender_type: str = "User", sender_id: str = "user-1"
) -> MagicMock:
    """Stand-in for a PlatformMessage from get_next_message. The drain loop
    reads ``id``, ``sender_type``, and ``sender_id``.
    """
    m = MagicMock()
    m.id = msg_id
    m.sender_type = sender_type
    m.sender_id = sender_id
    return m


def _ctx_item(
    msg_id: str,
    *,
    content: str = "hi",
    sender_id: str = "user-1",
    sender_type: str = "User",
    sender_name: str = "Alice",
) -> MagicMock:
    """A context item as returned by get_agent_chat_context. Real string
    attributes so context_item_to_dict + format_history_for_llm don't choke.
    """
    item = MagicMock()
    item.id = msg_id
    item.content = content
    item.sender_id = sender_id
    item.sender_type = sender_type
    item.sender_name = sender_name
    item.message_type = "user"
    item.metadata = {}
    item.inserted_at = "2026-05-21T10:00:00Z"
    return item


def _make_link_mock(
    participants: list[dict[str, Any]] | None = None,
    history_items: list[Any] | None = None,
    next_messages: list[MagicMock | None] | None = None,
    *,
    agent_name: str = "TestBot",
    agent_description: str = "a test agent",
) -> MagicMock:
    """Build a fake ThenvoiLink.

    ``next_messages`` controls successive ``get_next_message`` returns; once
    exhausted, all further calls return ``None``. The identity endpoint is
    stubbed so ``startup()`` succeeds.
    """
    link = MagicMock()

    # Identity (for startup()).
    agent_me = MagicMock()
    agent_me.name = agent_name
    agent_me.description = agent_description
    identity_response = MagicMock()
    identity_response.data = agent_me
    link.rest.agent_api_identity.get_agent_me = AsyncMock(
        return_value=identity_response
    )

    # Participants.
    participants_response = MagicMock()
    participants_response.data = [
        _make_participant_mock(p) for p in (participants or [])
    ] or None
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=participants_response
    )

    # History / context.
    context_response = MagicMock()
    context_response.data = history_items or []
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=context_response
    )

    # Lifecycle markers.
    sequence = list(next_messages or [])

    async def _get_next(*_args: Any, **_kwargs: Any) -> MagicMock | None:
        return sequence.pop(0) if sequence else None

    link.get_next_message = AsyncMock(side_effect=_get_next)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.disconnect = AsyncMock()
    return link


def _make_adapter_mock() -> MagicMock:
    adapter = MagicMock()
    adapter.on_started = AsyncMock()
    adapter.on_event = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    return adapter


async def _make_invoker(
    link: MagicMock,
    adapter: MagicMock | None = None,
    *,
    agent_id: str = "agent-1",
    drain_cap: int = 50,
) -> OneShotInvoker:
    invoker = OneShotInvoker(
        link=link,
        adapter=adapter or _make_adapter_mock(),
        agent_id=agent_id,
        drain_cap=drain_cap,
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
        msg = _build_platform_message(payload, "r1", "Alice")
        assert msg.id == "m1"
        assert msg.room_id == "r1"
        assert msg.content == "hello"
        assert msg.sender_id == "u1"
        assert msg.sender_type == "User"
        assert msg.sender_name == "Alice"
        assert msg.message_type == "user"
        assert msg.created_at.year == 2026

    def test_defaults_for_missing_fields(self) -> None:
        msg = _build_platform_message({"id": "m1"}, "r1", None)
        assert msg.content == ""
        assert msg.sender_id == ""
        assert msg.sender_type == "User"
        assert msg.message_type == "user"


# ---------------------------------------------------------------------------
# Startup / lifecycle
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_fetches_metadata_and_primes_adapter(self) -> None:
        link = _make_link_mock(agent_name="Weather", agent_description="forecasts")
        adapter = _make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()

        assert invoker.agent_name == "Weather"
        assert invoker.agent_description == "forecasts"
        # Adapter primed with identity + metadata.
        assert getattr(adapter, "_thenvoi_agent_id") == "agent-1"
        adapter.on_started.assert_awaited_once_with("Weather", "forecasts")

    async def test_startup_is_idempotent(self) -> None:
        link = _make_link_mock()
        adapter = _make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()
        await invoker.startup()

        link.rest.agent_api_identity.get_agent_me.assert_awaited_once()
        adapter.on_started.assert_awaited_once()

    async def test_handle_event_before_startup_raises(self) -> None:
        link = _make_link_mock()
        invoker = OneShotInvoker(
            link=link, adapter=_make_adapter_mock(), agent_id="agent-1"
        )
        with pytest.raises(RuntimeError, match="startup"):
            await invoker.handle_event(_msg_body())

    async def test_shutdown_disconnects_link(self) -> None:
        link = _make_link_mock()
        invoker = await _make_invoker(link)
        await invoker.shutdown()
        link.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_event routing
# ---------------------------------------------------------------------------


class TestHandleEventRouting:
    async def test_non_message_event_returns_ignored(self) -> None:
        link = _make_link_mock()
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(
            {"event_type": "room_added", "room_id": "r1", "payload": {}}
        )
        assert result["status"] == "ignored"
        assert result["event_type"] == "room_added"
        adapter.on_event.assert_not_awaited()
        link.get_next_message.assert_not_awaited()

    async def test_missing_room_id_raises_envelope_error(self) -> None:
        link = _make_link_mock()
        invoker = await _make_invoker(link)
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "payload": {"id": "msg-1", "sender_id": "u", "content": "x"},
        }
        with pytest.raises(OneShotEnvelopeError, match="room_id"):
            await invoker.handle_event(body)

    async def test_missing_message_id_raises_envelope_error(self) -> None:
        link = _make_link_mock()
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
        link = _make_link_mock(next_messages=[_platform_msg("msg-1"), None])
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
        link = _make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            next_messages=[_platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        assert result["status"] == "done"
        assert result["room_id"] == "room-1"
        assert result["message_id"] == "msg-1"

        adapter.on_event.assert_awaited_once()
        inp = adapter.on_event.call_args.args[0]
        assert inp.msg.id == "msg-1"
        assert inp.msg.sender_name == "Alice"

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

    async def test_skips_self_message(self) -> None:
        link = _make_link_mock()
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
        link = _make_link_mock(next_messages=[None])
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body())

        assert result["status"] == "no_pending"
        assert result["message_id"] == "msg-1"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_different_message_is_next(self) -> None:
        link = _make_link_mock(next_messages=[_platform_msg("msg-other")])
        adapter = _make_adapter_mock()
        invoker = await _make_invoker(link, adapter)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        assert result["status"] == "already_processed"
        assert result["message_id"] == "msg-1"
        assert result["next_open"] == "msg-other"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_marks_failed_on_adapter_error(self) -> None:
        link = _make_link_mock(next_messages=[_platform_msg("msg-1")])
        adapter = _make_adapter_mock()
        adapter.on_event = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        invoker = await _make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="LLM crashed"):
            await invoker.handle_event(_msg_body())

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_awaited_once()
        link.mark_processed.assert_not_awaited()


# ---------------------------------------------------------------------------
# Drain (race fix + self-skip + cap surfacing)
# ---------------------------------------------------------------------------


class TestDrain:
    async def test_drains_messages_seen_by_llm(self) -> None:
        """The case drain is for: the LLM saw msg-2 and msg-3 in its history
        snapshot. Drain marks them processed without re-invoking the LLM.
        """
        link = _make_link_mock(
            history_items=[_ctx_item("msg-2"), _ctx_item("msg-3")],
            next_messages=[
                _platform_msg("msg-1"),  # claim check
                _platform_msg("msg-2"),  # drain (in snapshot)
                _platform_msg("msg-3"),  # drain (in snapshot)
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
        link = _make_link_mock(
            history_items=[_ctx_item("msg-1")],  # snapshot = msg-1 only
            next_messages=[
                _platform_msg("msg-1"),  # claim check
                _platform_msg("msg-2"),  # arrived after snapshot → leave open
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
        self_msg = _platform_msg("msg-self", sender_type="Agent", sender_id="agent-1")
        link = _make_link_mock(
            history_items=[_ctx_item("msg-1"), _ctx_item("msg-self")],
            next_messages=[
                _platform_msg("msg-1"),  # claim check
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
        always_stale = _platform_msg("msg-x")
        link = _make_link_mock(history_items=[_ctx_item("msg-x")])
        link.get_next_message = AsyncMock(
            side_effect=[_platform_msg("msg-1")]  # claim check
            + [always_stale] * 10  # drain keeps finding msg-x
        )
        invoker = await _make_invoker(link, _make_adapter_mock(), drain_cap=3)

        result = await invoker.handle_event(_msg_body(msg_id="msg-1"))

        assert result["status"] == "done"
        assert result.get("drain_truncated") is True
