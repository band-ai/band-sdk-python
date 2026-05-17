"""Tests for the wrapping-shape SlackAdapter (Steps 3+4+6.5 reworked).

Architecture under test:

- ``SlackAdapter`` wraps an ``inner`` framework adapter (the brain).
- The brain sees two outbound tools when the room is Slack-bound:
  - ``thenvoi_send_message`` — real Thenvoi message (requires mentions)
  - ``slack_send_message`` — posts to the bound Slack thread, Slack-only
- A Slack event → adapter creates/finds a Thenvoi room → synthesizes a
  ``PlatformMessage`` → invokes ``inner.on_message`` with the new
  ``_SlackTeeingTools`` and a Slack-context note via ``participants_msg``.
- No event mirroring of inbound Slack messages or brain replies. The
  Thenvoi room stays empty unless the brain decides to delegate to a peer
  via ``thenvoi_send_message``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import PlatformMessage
from thenvoi.integrations.slack.adapter import (
    SLACK_CONTEXT_NOTE,
    SLACK_SEND_MESSAGE_TOOL_NAME,
    SlackAdapter,
    _SlackTeeingTools,
)
from thenvoi.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from thenvoi.integrations.slack.types import SlackApp, SlackRoomBinding
from thenvoi.runtime.tools import AgentTools


# ── Test doubles ─────────────────────────────────────────────────────────────


class _RawHistoryConverter:
    """Identity converter — returns the raw history list unchanged.

    Lets tests inspect exactly what the SlackAdapter synthesized for the
    brain before any framework-specific reshaping.
    """

    def convert(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(raw)


class _SlackReplyBrain(SimpleAdapter[Any]):
    """Brain that replies to Slack via the new ``slack_send_message`` tool.

    Calls ``await tools.slack_send_message(reply)`` directly to mimic how
    a real framework adapter would dispatch the tool the LLM picked.
    """

    def __init__(
        self,
        reply: str | None = "Here is the answer.",
        history_converter: Any = None,
    ) -> None:
        super().__init__(history_converter=history_converter)
        self.reply = reply
        self.invocations: list[dict[str, Any]] = []
        self.started: tuple[str, str] | None = None
        self.cleaned_up: list[str] = []

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        self.started = (agent_name, agent_description)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: Any,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.invocations.append(
            {
                "msg": msg,
                "tools": tools,
                "history": history,
                "participants_msg": participants_msg,
                "contacts_msg": contacts_msg,
                "is_session_bootstrap": is_session_bootstrap,
                "room_id": room_id,
            }
        )
        if self.reply is not None and hasattr(tools, "slack_send_message"):
            await tools.slack_send_message(self.reply)

    async def on_cleanup(self, room_id: str) -> None:
        self.cleaned_up.append(room_id)


def _make_rest_mock(room_ids: list[str]) -> MagicMock:
    rest = MagicMock()
    create_chat_calls = {"i": 0}

    async def create_chat(*, chat, **_kwargs):
        i = create_chat_calls["i"]
        create_chat_calls["i"] += 1
        return SimpleNamespace(data=SimpleNamespace(id=room_ids[i]))

    rest.agent_api_chats.create_agent_chat = AsyncMock(side_effect=create_chat)
    rest.agent_api_events.create_agent_chat_event = AsyncMock()
    rest.agent_api_messages.create_agent_chat_message = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="msg-id"))
    )
    rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
    return rest


def _slack_app(slug: str = "dev") -> SlackApp:
    return SlackApp(
        slug=slug,
        signing_secret="test-secret",
        bot_token=f"xoxb-{slug}",
    )


def _make_adapter(
    *,
    inner: SimpleAdapter[Any] | None = None,
    apps: list[SlackApp] | None = None,
    room_ids: list[str] | None = None,
    bridge_agent_id: str = "bridge-uuid",
) -> tuple[
    SlackAdapter,
    _SlackReplyBrain | SimpleAdapter[Any],
    dict[str, AsyncMock],
    MagicMock,
]:
    inner = inner or _SlackReplyBrain()
    apps = apps or [_slack_app()]
    room_ids = room_ids or ["room-1"]

    web_mocks: dict[str, AsyncMock] = {}
    for app in apps:
        client = AsyncMock()
        client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "x"})
        client.assistant_threads_setStatus = AsyncMock(return_value={"ok": True})
        # Default: thread has no prior messages. Tests can override per
        # client (e.g. ``web_mocks["dev"].conversations_replies = ...``).
        client.conversations_replies = AsyncMock(return_value={"messages": []})
        web_mocks[app.slug] = client

    rest = _make_rest_mock(room_ids)
    adapter = SlackAdapter(
        inner=inner,
        apps=apps,
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda a: web_mocks[a.slug],
    )
    adapter._thenvoi_agent_id = bridge_agent_id  # type: ignore[attr-defined]
    return adapter, inner, web_mocks, rest


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"{SLACK_SIGNATURE_VERSION}={digest}"


async def _post_slack_event(
    adapter: SlackAdapter, app: SlackApp, payload: dict[str, Any]
) -> httpx.Response:
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = _sign(app.signing_secret, body, timestamp)
    transport = ASGITransport(app=adapter.router)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/{app.slug}/events",
            content=body,
            headers={
                "x-slack-request-timestamp": timestamp,
                "x-slack-signature": signature,
                "content-type": "application/json",
            },
        )


def _mention_event(
    *,
    channel: str = "C123",
    ts: str = "1700000000.0001",
    text: str = "<@U001> hello",
    thread_ts: str | None = None,
    user: str = "U999",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "app_mention",
        "channel": channel,
        "ts": ts,
        "text": text,
        "user": user,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return {"type": "event_callback", "event": event}


# ── on_started ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_started_propagates_to_inner_and_sets_agent_id():
    adapter, inner, _, _ = _make_adapter()
    assert isinstance(inner, _SlackReplyBrain)

    await adapter.on_started("MyBot", "describes me")

    assert inner.started == ("MyBot", "describes me")
    assert getattr(inner, "_thenvoi_agent_id", None) == "bridge-uuid"


@pytest.mark.asyncio
async def test_on_started_requires_api_key_when_no_rest_client_injected():
    inner = _SlackReplyBrain()
    adapter = SlackAdapter(inner=inner, apps=[_slack_app()])
    with pytest.raises(ValueError, match="requires api_key"):
        await adapter.on_started("MyBot", "")


# ── Slack ingress (HTTP webhook → brain invocation) ─────────────────────────


@pytest.mark.asyncio
async def test_slack_event_creates_room_invokes_brain_and_replies_via_tool():
    adapter, inner, web_mocks, rest = _make_adapter(
        inner=_SlackReplyBrain(reply="Hello, world."),
        room_ids=["room-1"],
    )
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    response = await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C123",
            ts="1700000000.000100",
            text="<@U001> ping",
            user="U999",
        ),
    )
    assert response.status_code == 200
    await adapter.wait_idle()

    # Thenvoi room was created (delegation infrastructure).
    rest.agent_api_chats.create_agent_chat.assert_awaited_once()

    # One event emitted: the bootstrap context event for Step 8
    # rehydration. NO inbound relay event, NO brain-reply mirror event.
    assert rest.agent_api_events.create_agent_chat_event.await_count == 1
    context_evt = rest.agent_api_events.create_agent_chat_event.await_args.kwargs[
        "event"
    ]
    assert context_evt.message_type == "task"
    assert context_evt.metadata["slack_channel_id"] == "C123"

    # No regular Thenvoi messages posted — the brain replied via Slack only.
    rest.agent_api_messages.create_agent_chat_message.assert_not_awaited()

    # Brain invoked with clean synthesized PlatformMessage + Slack-context note.
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    inv = inner.invocations[0]
    assert inv["msg"].content == "<@U001> ping"
    assert inv["msg"].sender_id == "slack:U999"
    assert inv["msg"].metadata["slack_channel_id"] == "C123"
    assert inv["participants_msg"] == SLACK_CONTEXT_NOTE
    assert inv["is_session_bootstrap"] is True
    # Tools are the teeing subclass.
    assert isinstance(inv["tools"], _SlackTeeingTools)

    # Brain's reply went to Slack only.
    web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
        channel="C123",
        text="Hello, world.",
        thread_ts="1700000000.000100",
    )


@pytest.mark.asyncio
async def test_second_event_in_same_thread_reuses_room():
    adapter, inner, _, rest = _make_adapter(room_ids=["room-1", "room-2"])
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="first")
    )
    await adapter.wait_idle()
    await _post_slack_event(
        adapter,
        app,
        _mention_event(channel="C1", ts="200.0", thread_ts="100.0", text="follow-up"),
    )
    await adapter.wait_idle()

    assert rest.agent_api_chats.create_agent_chat.await_count == 1
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.invocations[0]["is_session_bootstrap"] is True
    assert inner.invocations[1]["is_session_bootstrap"] is False


@pytest.mark.asyncio
async def test_dm_event_creates_room_and_invokes_brain():
    adapter, inner, _, rest = _make_adapter()
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter,
        app,
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D123",
                "ts": "1700000000.0",
                "text": "ping",
                "user": "U999",
            },
        },
    )
    await adapter.wait_idle()

    rest.agent_api_chats.create_agent_chat.assert_awaited_once()
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    assert inner.invocations[0]["msg"].content == "ping"


@pytest.mark.asyncio
async def test_bot_authored_messages_are_ignored():
    adapter, inner, _, rest = _make_adapter()
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter,
        app,
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "ts": "1.0",
                "text": "echo: previous",
                "bot_id": "B1",
            },
        },
    )
    await adapter.wait_idle()

    rest.agent_api_chats.create_agent_chat.assert_not_awaited()
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.invocations == []


@pytest.mark.asyncio
async def test_unsupported_event_type_is_ignored():
    adapter, inner, _, rest = _make_adapter()
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    response = await _post_slack_event(
        adapter,
        app,
        {
            "type": "event_callback",
            "event": {"type": "team_join", "user": {"id": "U001"}},
        },
    )
    assert response.status_code == 200
    await adapter.wait_idle()
    rest.agent_api_chats.create_agent_chat.assert_not_awaited()
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.invocations == []


# ── Thenvoi WS path (on_message delegation) ─────────────────────────────────


@pytest.mark.asyncio
async def test_on_message_delegates_to_inner_for_unbound_room():
    """A peer-to-peer message in a room we never bound to Slack."""
    adapter, inner, _, _ = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")

    fake_real_tools = AgentTools(
        room_id="unrelated-room",
        rest=MagicMock(),
        participants=[],
    )

    msg = PlatformMessage(
        id="m1",
        room_id="unrelated-room",
        content="hi from peer",
        sender_id="peer-xyz",
        sender_type="peer",
        sender_name="Peer",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    await adapter.on_message(
        msg,
        fake_real_tools,
        None,
        None,
        None,
        is_session_bootstrap=False,
        room_id="unrelated-room",
    )

    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    # Unbound: raw tools, no Slack context note.
    assert not isinstance(inner.invocations[0]["tools"], _SlackTeeingTools)
    assert inner.invocations[0]["participants_msg"] is None


@pytest.mark.asyncio
async def test_on_message_wraps_tools_and_injects_note_for_bound_room():
    """When a peer messages in a Slack-bound room, brain gets the tee + note."""
    adapter, inner, web_mocks, rest = _make_adapter()
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    # Seed a Slack-bound room.
    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="initial")
    )
    await adapter.wait_idle()
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    web_mocks[app.slug].chat_postMessage.reset_mock()

    # Now simulate a WS-delivered peer message in the same bound room.
    real_tools = AgentTools(room_id="room-1", rest=rest, participants=[])
    msg = PlatformMessage(
        id="m2",
        room_id="room-1",
        content="peer joined and said hi",
        sender_id="peer-x",
        sender_type="peer",
        sender_name="Peer X",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    await adapter.on_message(
        msg,
        real_tools,
        None,
        None,
        None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    assert len(inner.invocations) == 2
    inv = inner.invocations[1]
    assert isinstance(inv["tools"], _SlackTeeingTools)
    assert inv["participants_msg"] == SLACK_CONTEXT_NOTE
    # Brain's reply ('Here is the answer.') went to Slack.
    web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
        channel="C1", text="Here is the answer.", thread_ts="100.0"
    )


@pytest.mark.asyncio
async def test_on_message_merges_existing_participants_msg_with_context_note():
    """Caller-provided participants_msg is preserved when we inject our note."""
    adapter, inner, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="hi")
    )
    await adapter.wait_idle()
    assert isinstance(inner, _SlackReplyBrain)
    inner.invocations.clear()

    real_tools = AgentTools(room_id="room-1", rest=rest, participants=[])
    msg = PlatformMessage(
        id="m3",
        room_id="room-1",
        content="peer text",
        sender_id="peer-y",
        sender_type="peer",
        sender_name="Peer Y",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    await adapter.on_message(
        msg,
        real_tools,
        None,
        "@joe joined the room",
        None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    merged = inner.invocations[0]["participants_msg"]
    assert SLACK_CONTEXT_NOTE in merged
    assert "@joe joined the room" in merged


# ── on_cleanup ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_cleanup_drops_binding_and_calls_inner():
    adapter, inner, _, _ = _make_adapter()
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(adapter, app, _mention_event(channel="C1", ts="100.0"))
    await adapter.wait_idle()
    assert "room-1" in adapter._room_to_binding

    await adapter.on_cleanup("room-1")

    assert "room-1" not in adapter._room_to_binding
    assert "C1:100.0" not in adapter._thread_to_room
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.cleaned_up == ["room-1"]


# ── Invariants we still want ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ack_returns_before_brain_finishes():
    """Slack ack must beat the brain's slow LLM call."""

    class _SlowBrain(SimpleAdapter[Any]):
        def __init__(self) -> None:
            super().__init__(history_converter=None)
            self.finished = asyncio.Event()

        async def on_message(
            self, msg, tools, history, p, c, *, is_session_bootstrap, room_id
        ):
            await asyncio.sleep(0.3)
            self.finished.set()

        async def on_cleanup(self, room_id: str) -> None:
            return None

    brain = _SlowBrain()
    adapter, _, _, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    start = time.monotonic()
    response = await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0")
    )
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert not brain.finished.is_set()
    assert elapsed < 0.2

    await adapter.wait_idle()
    assert brain.finished.is_set()


@pytest.mark.asyncio
async def test_brain_exception_does_not_break_subsequent_events():
    """A crashing brain on event #1 shouldn't poison event #2."""
    calls: list[str] = []

    class _CrashingBrain(SimpleAdapter[Any]):
        def __init__(self) -> None:
            super().__init__(history_converter=None)

        async def on_message(
            self, msg, tools, history, p, c, *, is_session_bootstrap, room_id
        ):
            calls.append(msg.content)
            if len(calls) == 1:
                raise RuntimeError("transient brain error")

        async def on_cleanup(self, room_id: str) -> None:
            return None

    adapter, _, _, _ = _make_adapter(inner=_CrashingBrain(), room_ids=["r1", "r2"])
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="first")
    )
    await adapter.wait_idle()
    await _post_slack_event(
        adapter, app, _mention_event(channel="C2", ts="200.0", text="second")
    )
    await adapter.wait_idle()

    assert calls == ["first", "second"]


# ── _SlackTeeingTools — new behavior ─────────────────────────────────────────


def _make_tee_tools(
    slack: AsyncMock | None = None,
) -> tuple[_SlackTeeingTools, MagicMock, AsyncMock]:
    rest = MagicMock()
    rest.agent_api_events.create_agent_chat_event = AsyncMock()
    rest.agent_api_messages.create_agent_chat_message = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="m"))
    )
    base = AgentTools(room_id="r1", rest=rest, participants=[])
    if slack is None:
        # Only set defaults when constructing a fresh mock; a caller-provided
        # ``slack`` may have custom side_effects we mustn't overwrite.
        slack = AsyncMock()
        slack.chat_postMessage = AsyncMock(return_value={"ok": True})
    tools = _SlackTeeingTools(
        wrap=base,
        slack=slack,
        binding=SlackRoomBinding(app_slug="dev", channel="C", thread_ts="1.0"),
    )
    return tools, rest, slack


@pytest.mark.asyncio
async def test_slack_send_message_posts_to_slack_only():
    tools, rest, slack = _make_tee_tools()
    result = await tools.slack_send_message("hello")

    assert result == {"ok": True}
    slack.chat_postMessage.assert_awaited_once_with(
        channel="C", text="hello", thread_ts="1.0"
    )
    # Crucially: nothing posted to Thenvoi REST.
    rest.agent_api_events.create_agent_chat_event.assert_not_awaited()
    rest.agent_api_messages.create_agent_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_send_message_returns_error_dict_on_failure():
    slack = AsyncMock()
    slack.chat_postMessage = AsyncMock(side_effect=RuntimeError("kaboom"))
    tools, _, _ = _make_tee_tools(slack=slack)

    result = await tools.slack_send_message("hello")
    assert result["ok"] is False
    assert "kaboom" in result["error"]


def test_get_anthropic_tool_schemas_includes_slack_send_message():
    tools, _, _ = _make_tee_tools()
    schemas = tools.get_anthropic_tool_schemas()
    names = [s["name"] for s in schemas]
    assert SLACK_SEND_MESSAGE_TOOL_NAME in names
    # The standard Thenvoi tools are still present (sanity check).
    assert "thenvoi_send_message" in names


def test_get_openai_tool_schemas_includes_slack_send_message():
    tools, _, _ = _make_tee_tools()
    schemas = tools.get_openai_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert SLACK_SEND_MESSAGE_TOOL_NAME in names
    assert "thenvoi_send_message" in names


def test_base_agent_tools_does_not_include_slack_tool():
    """The slack tool is only exposed via the teeing subclass."""
    rest = MagicMock()
    base = AgentTools(room_id="r", rest=rest, participants=[])
    schemas = base.get_anthropic_tool_schemas()
    names = [s["name"] for s in schemas]
    assert SLACK_SEND_MESSAGE_TOOL_NAME not in names


@pytest.mark.asyncio
async def test_execute_tool_call_routes_slack_send_message():
    tools, _, slack = _make_tee_tools()
    result = await tools.execute_tool_call(
        SLACK_SEND_MESSAGE_TOOL_NAME, {"content": "hi from llm"}
    )
    assert result == {"ok": True}
    slack.chat_postMessage.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_empty_content():
    tools, _, slack = _make_tee_tools()
    result = await tools.execute_tool_call(
        SLACK_SEND_MESSAGE_TOOL_NAME, {"content": ""}
    )
    assert isinstance(result, str)
    assert "requires a non-empty 'content'" in result
    slack.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_call_delegates_non_slack_tools_to_super():
    """Tools other than ``slack_send_message`` flow through to AgentTools."""
    from unittest.mock import patch

    tools, _, _ = _make_tee_tools()
    super_mock = AsyncMock(return_value="ok")
    with patch.object(AgentTools, "execute_tool_call", super_mock):
        result = await tools.execute_tool_call("thenvoi_lookup_peers", {})

    assert result == "ok"
    super_mock.assert_awaited_once_with("thenvoi_lookup_peers", {})


@pytest.mark.asyncio
async def test_send_message_no_longer_overridden_uses_real_thenvoi_path():
    """The base AgentTools.send_message behavior is restored (mention required)."""
    from thenvoi.core.exceptions import ThenvoiToolError

    tools, _, slack = _make_tee_tools()
    # Mentionless message hits the platform's "≥1 mention required" guard.
    with pytest.raises(ThenvoiToolError, match="mention is required"):
        await tools.send_message("hi", mentions=None)
    # No Slack tee for thenvoi_send_message path.
    slack.chat_postMessage.assert_not_awaited()


# ── Step 7.5: channel-mention coverage + thread history backfill ────────────


@pytest.mark.asyncio
async def test_channel_top_level_mention_does_not_fetch_thread_history():
    """(a) Top-level @mention in a channel: thread_ts == ts, no backfill."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter,
        app,
        _mention_event(channel="C123", ts="100.0", text="<@U001> hi"),
    )
    await adapter.wait_idle()

    web_mocks[app.slug].conversations_replies.assert_not_awaited()
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.invocations[0]["history"] == []


@pytest.mark.asyncio
async def test_channel_mention_in_existing_thread_pulls_history():
    """(b) @mention inside an existing thread: pull conversations.replies once."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    # Slack returns the full thread when we ask. Includes a human-only
    # exchange that happened before the trigger plus the trigger itself
    # (which we must filter out).
    web_mocks[app.slug].conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"ts": "100.0", "user": "U001", "text": "hey team"},
                {"ts": "101.0", "user": "U002", "text": "anyone seen the report?"},
                {"ts": "102.0", "user": "U003", "text": "<@BOT> summarize"},
            ]
        }
    )

    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C123",
            ts="102.0",
            thread_ts="100.0",
            text="<@BOT> summarize",
            user="U003",
        ),
    )
    await adapter.wait_idle()

    web_mocks[app.slug].conversations_replies.assert_awaited_once_with(
        channel="C123",
        ts="100.0",
    )

    assert isinstance(inner, _SlackReplyBrain)
    history = inner.invocations[0]["history"]
    # Trigger excluded; remaining pre-mention turns preserved in order.
    assert [h["content"] for h in history] == ["hey team", "anyone seen the report?"]
    assert all(h["role"] == "user" for h in history)
    assert all(h["sender_type"] == "user" for h in history)
    assert [h["sender_name"] for h in history] == ["slack:U001", "slack:U002"]


@pytest.mark.asyncio
async def test_subsequent_mention_in_same_thread_refetches_history():
    """(c) Slack does not deliver thread context with each event; the adapter
    must refetch on every threaded mention so stateless brains see the
    growing conversation. Slack's own Bolt JS reference does the same.
    The same Thenvoi room is reused (no second ``create_agent_chat``)."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, _, web_mocks, rest = _make_adapter(
        inner=brain,
        room_ids=["room-1", "room-2"],
    )
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    web_mocks[app.slug].conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"ts": "50.0", "user": "U001", "text": "earlier discussion"},
                {"ts": "100.0", "user": "U002", "text": "<@BOT> first mention"},
            ]
        }
    )

    # First mention mid-thread → fetch.
    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C1",
            ts="100.0",
            thread_ts="50.0",
            text="<@BOT> first mention",
            user="U002",
        ),
    )
    await adapter.wait_idle()
    assert web_mocks[app.slug].conversations_replies.await_count == 1

    # Second mention in the same thread → refetch (Slack's recommended
    # pattern), but same Thenvoi room.
    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C1",
            ts="200.0",
            thread_ts="50.0",
            text="<@BOT> follow up",
            user="U002",
        ),
    )
    await adapter.wait_idle()
    assert web_mocks[app.slug].conversations_replies.await_count == 2
    # Only one Thenvoi room created across both mentions.
    assert rest.agent_api_chats.create_agent_chat.await_count == 1


@pytest.mark.asyncio
async def test_bot_replies_in_thread_history_become_assistant_role():
    """Self-replies in the fetched thread are tagged as assistant turns."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    web_mocks[app.slug].conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"ts": "100.0", "user": "U001", "text": "hi bot"},
                {
                    "ts": "101.0",
                    "user": "UBOT",
                    "bot_id": "B999",
                    "text": "hello, how can I help?",
                },
                {"ts": "102.0", "user": "U001", "text": "<@BOT> what time is it?"},
            ]
        }
    )

    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C1",
            ts="102.0",
            thread_ts="100.0",
            text="<@BOT> what time is it?",
            user="U001",
        ),
    )
    await adapter.wait_idle()

    assert isinstance(inner, _SlackReplyBrain)
    history = inner.invocations[0]["history"]
    assert len(history) == 2
    user_turn, bot_turn = history
    assert user_turn["role"] == "user"
    assert user_turn["sender_type"] == "user"
    assert user_turn["content"] == "hi bot"
    # Bot turn is preserved as assistant context, labeled with this agent's
    # name so framework converters route it as a prior self-turn.
    assert bot_turn["role"] == "assistant"
    assert bot_turn["sender_type"] == "Agent"
    assert bot_turn["sender_name"] == "MyBot"
    assert bot_turn["content"] == "hello, how can I help?"


@pytest.mark.asyncio
async def test_fresh_dm_does_not_fetch_thread_history():
    """A brand-new DM (thread_ts == ts, new room) must not call replies."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter,
        app,
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D1",
                "ts": "1700000000.0",
                "text": "ping",
                "user": "U999",
            },
        },
    )
    await adapter.wait_idle()

    web_mocks[app.slug].conversations_replies.assert_not_awaited()
    assert isinstance(inner, _SlackReplyBrain)
    assert inner.invocations[0]["history"] == []


@pytest.mark.asyncio
async def test_thread_history_fetch_failure_falls_back_to_empty_history():
    """A failed conversations.replies must not break the trigger reply."""
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    web_mocks[app.slug].conversations_replies = AsyncMock(
        side_effect=RuntimeError("missing_scope: channels:history")
    )

    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C1",
            ts="200.0",
            thread_ts="100.0",
            text="<@BOT> summarize",
            user="U001",
        ),
    )
    await adapter.wait_idle()

    web_mocks[app.slug].conversations_replies.assert_awaited_once()
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    assert inner.invocations[0]["history"] == []
