"""Tests for examples/agentcore/agentcore_llm_server.py (the AgentCore container).

The container is in examples/, so we add it to sys.path here. Tests focus on
the request-processing logic, not the FastAPI lifespan (which talks to a
real Thenvoi REST endpoint at startup).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_CONTAINER_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "agentcore"
    / "agentcore_llm_server.py"
)


def _load_container_module():
    spec = importlib.util.spec_from_file_location(
        "agentcore_llm_server", _CONTAINER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Provide env vars so the module imports cleanly (lifespan is lazy, not eager)
    os.environ.setdefault("THENVOI_AGENT_ID", "test-agent")
    os.environ.setdefault("THENVOI_API_KEY", "test-key")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic")
    spec.loader.exec_module(module)
    sys.modules["agentcore_llm_server"] = module
    return module


container = _load_container_module()


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestParseInsertedAt:
    def test_parses_iso_z(self) -> None:
        dt = container._parse_inserted_at("2026-05-21T10:00:00Z")
        assert dt.year == 2026 and dt.month == 5 and dt.day == 21
        assert dt.tzinfo is not None

    def test_parses_iso_offset(self) -> None:
        dt = container._parse_inserted_at("2026-05-21T10:00:00+00:00")
        assert dt.tzinfo is not None

    def test_falls_back_to_now_on_invalid(self) -> None:
        before = datetime.now(timezone.utc)
        dt = container._parse_inserted_at("not a date")
        after = datetime.now(timezone.utc)
        assert before <= dt <= after

    def test_falls_back_to_now_on_none(self) -> None:
        dt = container._parse_inserted_at(None)
        assert dt.tzinfo is not None


class TestLookupSenderName:
    def test_finds_by_id(self) -> None:
        participants = [
            {"id": "u1", "name": "Alice"},
            {"id": "u2", "name": "Bob"},
        ]
        assert container._lookup_sender_name(participants, "u2") == "Bob"

    def test_returns_none_when_not_found(self) -> None:
        assert container._lookup_sender_name([{"id": "x", "name": "x"}], "y") is None

    def test_returns_none_for_empty_sender_id(self) -> None:
        assert container._lookup_sender_name([{"id": "x"}], None) is None
        assert container._lookup_sender_name([{"id": "x"}], "") is None


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
        msg = container._build_platform_message(payload, "r1", "Alice")
        assert msg.id == "m1"
        assert msg.room_id == "r1"
        assert msg.content == "hello"
        assert msg.sender_id == "u1"
        assert msg.sender_type == "User"
        assert msg.sender_name == "Alice"
        assert msg.message_type == "user"
        assert msg.created_at.year == 2026

    def test_defaults_for_missing_fields(self) -> None:
        msg = container._build_platform_message({"id": "m1"}, "r1", None)
        assert msg.content == ""
        assert msg.sender_id == ""
        assert msg.sender_type == "User"
        assert msg.message_type == "user"


class TestRequireEnv:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_VAR__", "value")
        assert container._require_env("__TEST_VAR__") == "value"

    def test_raises_on_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("__TEST_VAR__", raising=False)
        with pytest.raises(ValueError, match="__TEST_VAR__"):
            container._require_env("__TEST_VAR__")

    def test_raises_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("__TEST_VAR__", "   ")
        with pytest.raises(ValueError, match="__TEST_VAR__"):
            container._require_env("__TEST_VAR__")


# ---------------------------------------------------------------------------
# _process_message_event
# ---------------------------------------------------------------------------


def _make_participant_mock(p: dict[str, Any]) -> MagicMock:
    """Build a participant mock. ``name`` must be set after construction —
    passing ``name=`` to MagicMock() sets the mock's identity, not its
    ``.name`` attribute.
    """
    mock = MagicMock(id=p["id"], type=p["type"], handle=p.get("handle"))
    mock.name = p["name"]
    return mock


def _platform_msg(msg_id: str) -> MagicMock:
    """A stand-in for thenvoi.runtime.types.PlatformMessage — only ``id`` is read."""
    m = MagicMock()
    m.id = msg_id
    return m


def _make_link_mock(
    participants: list[dict[str, Any]] | None = None,
    history_items: list[Any] | None = None,
    next_messages: list[MagicMock | None] | None = None,
) -> MagicMock:
    """Build a fake ThenvoiLink.

    ``next_messages`` controls what ``link.get_next_message`` returns on
    successive calls. Each call pops the next entry; once exhausted, all
    further calls return ``None``. If not provided, ``get_next_message``
    always returns ``None`` (tests that don't exercise the lifecycle path
    will skip via the no_pending branch — set the sequence to drive the
    test through the happy path).
    """
    link = MagicMock()
    participants_response = MagicMock()
    participants_response.data = [
        _make_participant_mock(p) for p in (participants or [])
    ] or None
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=participants_response
    )
    context_response = MagicMock()
    context_response.data = history_items or []
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=context_response
    )

    # Lifecycle methods on the link itself (not via .rest.*).
    sequence = list(next_messages or [])

    async def _get_next(*_args: Any, **_kwargs: Any) -> MagicMock | None:
        return sequence.pop(0) if sequence else None

    link.get_next_message = AsyncMock(side_effect=_get_next)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    return link


def _make_adapter_mock() -> MagicMock:
    adapter = MagicMock()
    adapter.on_event = AsyncMock()
    return adapter


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


class TestProcessMessageEvent:
    async def test_processes_message(self) -> None:
        link = _make_link_mock(
            participants=[
                {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"},
            ],
            # First call: returns triggering msg → claim it.
            # Second call (drain): None → nothing more open.
            next_messages=[_platform_msg("msg-1"), None],
        )
        adapter = _make_adapter_mock()

        result = await container._process_message_event(
            _msg_body(), link=link, adapter=adapter, own_agent_id="agent-1"
        )

        assert result["status"] == "done"
        assert result["room_id"] == "room-1"
        assert result["message_id"] == "msg-1"

        adapter.on_event.assert_awaited_once()
        inp = adapter.on_event.call_args.args[0]
        assert inp.msg.id == "msg-1"
        assert inp.msg.sender_name == "Alice"

        # Lifecycle calls in expected order.
        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_processed.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_not_awaited()

    async def test_skips_self_message(self) -> None:
        link = _make_link_mock()
        adapter = _make_adapter_mock()
        body = _msg_body(
            msg_id="msg-self",
            sender_id="agent-1",
            sender_type="Agent",
            content="echo",
        )

        result = await container._process_message_event(
            body, link=link, adapter=adapter, own_agent_id="agent-1"
        )
        assert result["status"] == "skipped_self"
        adapter.on_event.assert_not_awaited()
        # Self-filter happens before any lifecycle interaction.
        link.get_next_message.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_no_pending(self) -> None:
        """get_next_message returns None — the triggering message is
        already processed (e.g. a sibling invocation drained it). The LLM
        must not run."""
        link = _make_link_mock(next_messages=[None])
        adapter = _make_adapter_mock()

        result = await container._process_message_event(
            _msg_body(), link=link, adapter=adapter, own_agent_id="agent-1"
        )

        assert result["status"] == "no_pending"
        assert result["message_id"] == "msg-1"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_skips_when_different_message_is_next(self) -> None:
        """get_next_message returns a different msg id — the triggering
        message is already processed (or behind an older open one). Skip."""
        link = _make_link_mock(next_messages=[_platform_msg("msg-other")])
        adapter = _make_adapter_mock()

        result = await container._process_message_event(
            _msg_body(msg_id="msg-1"),
            link=link,
            adapter=adapter,
            own_agent_id="agent-1",
        )

        assert result["status"] == "already_processed"
        assert result["message_id"] == "msg-1"
        assert result["next_open"] == "msg-other"
        adapter.on_event.assert_not_awaited()
        link.mark_processing.assert_not_awaited()

    async def test_drains_stale_messages_after_llm(self) -> None:
        """After the LLM completes, get_next_message keeps returning open
        messages (the LLM saw them in history but the platform hasn't
        marked them yet). Drain marks each through the full
        processing → processed transition without re-invoking the LLM."""
        link = _make_link_mock(
            next_messages=[
                _platform_msg("msg-1"),  # claim check
                _platform_msg("msg-2"),  # drain #1
                _platform_msg("msg-3"),  # drain #2
                None,  # drain done
            ],
        )
        adapter = _make_adapter_mock()

        result = await container._process_message_event(
            _msg_body(msg_id="msg-1"),
            link=link,
            adapter=adapter,
            own_agent_id="agent-1",
        )

        assert result["status"] == "done"
        assert result["drained"] == ["msg-2", "msg-3"]

        # LLM ran exactly once.
        adapter.on_event.assert_awaited_once()
        # Each message — triggering and drained — gets the full
        # processing → processed transition.
        processing_args = [c.args for c in link.mark_processing.await_args_list]
        assert processing_args == [
            ("room-1", "msg-1"),
            ("room-1", "msg-2"),
            ("room-1", "msg-3"),
        ]
        processed_args = [c.args for c in link.mark_processed.await_args_list]
        assert processed_args == [
            ("room-1", "msg-1"),
            ("room-1", "msg-2"),
            ("room-1", "msg-3"),
        ]

    async def test_marks_failed_on_adapter_error(self) -> None:
        """If the adapter raises, the triggering message is marked failed."""
        link = _make_link_mock(next_messages=[_platform_msg("msg-1")])
        adapter = _make_adapter_mock()
        adapter.on_event = AsyncMock(side_effect=RuntimeError("LLM crashed"))

        with pytest.raises(RuntimeError, match="LLM crashed"):
            await container._process_message_event(
                _msg_body(),
                link=link,
                adapter=adapter,
                own_agent_id="agent-1",
            )

        link.mark_processing.assert_awaited_once_with("room-1", "msg-1")
        link.mark_failed.assert_awaited_once()
        # mark_processed should NOT have been called on a failed message.
        link.mark_processed.assert_not_awaited()

    async def test_missing_room_id_raises(self) -> None:
        link = _make_link_mock()
        adapter = _make_adapter_mock()
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "payload": {"id": "msg-1", "sender_id": "u", "content": "x"},
        }
        with pytest.raises(HTTPException) as exc:
            await container._process_message_event(
                body, link=link, adapter=adapter, own_agent_id="agent-1"
            )
        assert exc.value.status_code == 400
        assert "room_id" in exc.value.detail

    async def test_missing_message_id_raises(self) -> None:
        link = _make_link_mock()
        adapter = _make_adapter_mock()
        body = {
            "event_type": "message_created",
            "agent_id": "agent-1",
            "room_id": "room-1",
            "payload": {"sender_id": "u", "content": "x"},
        }
        with pytest.raises(HTTPException) as exc:
            await container._process_message_event(
                body, link=link, adapter=adapter, own_agent_id="agent-1"
            )
        assert exc.value.status_code == 400

    async def test_falls_back_to_payload_chat_room_id(self) -> None:
        """When top-level room_id is missing, fall back to payload.chat_room_id."""
        link = _make_link_mock(next_messages=[_platform_msg("msg-1"), None])
        adapter = _make_adapter_mock()
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
        result = await container._process_message_event(
            body, link=link, adapter=adapter, own_agent_id="agent-1"
        )
        assert result["room_id"] == "fallback-room"


# ---------------------------------------------------------------------------
# FastAPI endpoint tests (no lifespan)
# ---------------------------------------------------------------------------


@pytest.fixture
def client_no_lifespan() -> TestClient:
    """TestClient that skips the real lifespan (no live Thenvoi calls).

    State is injected directly onto app.state by tests that need it.
    """
    # TestClient runs lifespan by default; we want to skip it because the real
    # lifespan calls Thenvoi REST.
    client = TestClient(container.app, raise_server_exceptions=False)
    # Inject default state for tests that don't override it.
    container.app.state.link = _make_link_mock()
    container.app.state.adapter = _make_adapter_mock()
    container.app.state.agent_id = "agent-1"
    return client


class TestPingEndpoint:
    def test_returns_healthy(self, client_no_lifespan: TestClient) -> None:
        # Bypass lifespan: call the route directly via the app's transport.
        # FastAPI's TestClient will run lifespan unless we use a transport
        # that doesn't; the simpler path is to call the handler function.
        import asyncio

        result = asyncio.run(container.ping())
        assert result == {"status": "Healthy"}


class TestInvocationsRouting:
    async def test_non_message_event_returns_ignored(self) -> None:
        # Directly call the endpoint logic with a fake request-like body
        # since /invocations only depends on body + app.state.
        container.app.state.link = _make_link_mock()
        container.app.state.adapter = _make_adapter_mock()
        container.app.state.agent_id = "agent-1"

        class _FakeRequest:
            async def json(self) -> dict[str, Any]:
                return {"event_type": "room_added", "room_id": "r1", "payload": {}}

        result = await container.invocations(_FakeRequest())  # type: ignore[arg-type]
        assert result["status"] == "ignored"
        container.app.state.adapter.on_event.assert_not_awaited()

    async def test_message_event_triggers_adapter(self) -> None:
        link = _make_link_mock(
            participants=[
                {"id": "u1", "name": "Alice", "type": "User", "handle": "alice"}
            ],
            next_messages=[_platform_msg("m1"), None],
        )
        adapter = _make_adapter_mock()
        container.app.state.link = link
        container.app.state.adapter = adapter
        container.app.state.agent_id = "agent-1"

        class _FakeRequest:
            async def json(self) -> dict[str, Any]:
                return {
                    "event_type": "message_created",
                    "agent_id": "agent-1",
                    "room_id": "r1",
                    "payload": {
                        "id": "m1",
                        "content": "hi @bot",
                        "sender_id": "u1",
                        "sender_type": "User",
                        "inserted_at": "2026-05-21T10:00:00Z",
                    },
                }

        result = await container.invocations(_FakeRequest())  # type: ignore[arg-type]
        assert result["status"] == "done"
        adapter.on_event.assert_awaited_once()
