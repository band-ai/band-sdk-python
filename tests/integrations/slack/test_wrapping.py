"""Tests for the wrapping-shape SlackAdapter.

Architecture under test:

- ``SlackAdapter`` wraps an ``inner`` framework adapter (the brain).
- The brain sees two outbound tools when the room is Slack-bound:
  - ``band_send_message`` — real Band message (requires mentions)
  - ``slack_send_message`` — posts to the bound Slack thread, Slack-only
- A Slack event → adapter creates/finds a Band room → synthesizes a
  ``PlatformMessage`` → invokes ``inner.on_message`` with the new
  ``_SlackTeeingTools`` and a Slack-context note via ``participants_msg``.
- No event mirroring of inbound Slack messages or brain replies. The
  Band room stays empty unless the brain decides to delegate to a peer
  via ``band_send_message``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import warnings
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AdapterFeatures,
    AgentInput,
    Capability,
    Emit,
    HistoryProvider,
    PlatformMessage,
)
from band.integrations.slack.adapter import (
    SLACK_CONTEXT_NOTE,
    SLACK_SEND_MESSAGE_TOOL_NAME,
    SlackAdapter,
    _SlackTeeingTools,
)
from band.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from band.integrations.slack.types import SlackApp, SlackRoomBinding
from band.runtime.tools import AgentTools


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
    **adapter_kwargs: Any,
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
        # This app's own bot identity (resolved via auth.test). Defaults to
        # "B999" so the thread-backfill tests that post bot replies with
        # ``bot_id="B999"`` are treated as *this bridge's* prior turns.
        client.auth_test = AsyncMock(return_value={"bot_id": "B999", "user_id": "UBOT"})
        # Default: thread has no prior messages. Tests can override per
        # client (e.g. ``web_mocks["dev"].conversations_replies = ...``).
        client.conversations_replies = AsyncMock(return_value={"messages": []})

        # Context-mirror label resolution. Defaults resolve any user to
        # display "Alice"/handle "alice", and derive a channel label from
        # the id (``C123`` -> ``#c123``; ids starting with ``D`` -> DM).
        client.users_info = AsyncMock(
            return_value={
                "user": {
                    "name": "alice",
                    "real_name": "Alice A",
                    "profile": {"display_name": "Alice", "real_name": "Alice A"},
                }
            }
        )

        def _conv_info(*, channel: str, **_kw: Any) -> dict[str, Any]:
            if channel.startswith("D"):
                return {"channel": {"is_im": True}}
            return {"channel": {"name": channel.lower()}}

        client.conversations_info = AsyncMock(side_effect=_conv_info)
        web_mocks[app.slug] = client

    rest = _make_rest_mock(room_ids)
    adapter = SlackAdapter(
        inner=inner,
        apps=apps,
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda a: web_mocks[a.slug],
        **adapter_kwargs,
    )
    adapter._band_agent_id = bridge_agent_id  # type: ignore[attr-defined]
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
    assert getattr(inner, "_band_agent_id", None) == "bridge-uuid"


@pytest.mark.asyncio
async def test_on_started_requires_api_key_when_no_rest_client_injected():
    inner = _SlackReplyBrain()
    adapter = SlackAdapter(inner=inner, apps=[_slack_app()])
    with pytest.raises(ValueError, match="requires api_key"):
        await adapter.on_started("MyBot", "")


class _EmitBrain(_SlackReplyBrain):
    """Inner brain that declares (and is configured to use) execution emit."""

    SUPPORTED_EMIT = frozenset({Emit.EXECUTION})
    SUPPORTED_CAPABILITIES = frozenset({Capability.MEMORY})


@pytest.mark.asyncio
async def test_on_started_mirrors_inner_support_no_spurious_warning(caplog):
    """SlackAdapter must not warn that it 'does not support' what the brain does.

    We adopt ``inner.features`` and delegate reasoning to the inner, so the
    base feature-mismatch check has to run against the inner's declared
    support — not the wrapper's empty defaults.
    """
    inner = _EmitBrain(reply=None)
    inner.features = AdapterFeatures(
        emit=frozenset({Emit.EXECUTION}),
        capabilities=frozenset({Capability.MEMORY}),
    )
    adapter, _, _, _ = _make_adapter(inner=inner)

    with caplog.at_level("WARNING"):
        with warnings.catch_warnings():
            # A spurious UserWarning here would mean the wrapper failed to
            # mirror the inner's support before the base check ran.
            warnings.simplefilter("error", UserWarning)
            await adapter.on_started("MyBot", "")

    # Wrapper now reflects the inner's declared support.
    assert adapter.SUPPORTED_EMIT == frozenset({Emit.EXECUTION})
    assert adapter.SUPPORTED_CAPABILITIES == frozenset({Capability.MEMORY})
    # No misleading "does not support" warning for values the brain handles.
    assert not any("does not support" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


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

    # Band room was created (delegation infrastructure).
    rest.agent_api_chats.create_agent_chat.assert_awaited_once()

    # Two events emitted: the bootstrap context (task) event, then the
    # user-turn mirror (thought) event. NO brain-reply mirror event —
    # only the inbound user turn is mirrored.
    emitted = [
        c.kwargs["event"]
        for c in rest.agent_api_events.create_agent_chat_event.await_args_list
    ]
    assert [e.message_type for e in emitted] == ["task", "thought"]
    context_evt = emitted[0]
    assert context_evt.metadata["slack_channel_id"] == "C123"
    # The mirror is a context-only thought carrying the Slack thread id.
    mirror_evt = emitted[1]
    assert mirror_evt.metadata["slack_mirror"] is True
    assert mirror_evt.metadata["slack_thread_ts"] == "1700000000.000100"
    assert mirror_evt.metadata["slack_user_id"] == "U999"
    # Friendly format: resolved channel name + user handle/name + text.
    # The raw thread ts lives in metadata only, not the visible content.
    assert "1700000000.000100" not in mirror_evt.content
    assert "#c123" in mirror_evt.content
    assert "Alice (@alice)" in mirror_evt.content
    assert "<@U001> ping" in mirror_evt.content
    assert mirror_evt.metadata["slack_channel_label"] == "#c123"
    assert mirror_evt.metadata["slack_user_handle"] == "alice"

    # No regular Band messages posted — the brain replied via Slack only.
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
async def test_same_thread_tuple_across_apps_maps_to_distinct_rooms():
    """Two SlackApps sharing a channel/thread tuple must get separate rooms.

    The room lookup key includes ``app.slug``, so a workspace-scoped
    ``channel:thread_ts`` collision between two apps can't cross-route
    replies into the wrong app/workspace.
    """
    apps = [_slack_app("alpha"), _slack_app("beta")]
    adapter, inner, _, rest = _make_adapter(
        apps=apps, room_ids=["room-alpha", "room-beta"]
    )
    await adapter.on_started("MyBot", "")

    # Identical channel + thread_ts, delivered to two different apps.
    await _post_slack_event(
        adapter, apps[0], _mention_event(channel="C1", ts="100.0", text="hi alpha")
    )
    await adapter.wait_idle()
    await _post_slack_event(
        adapter, apps[1], _mention_event(channel="C1", ts="100.0", text="hi beta")
    )
    await adapter.wait_idle()

    # Two distinct rooms, one per app — not a single shared room.
    assert rest.agent_api_chats.create_agent_chat.await_count == 2
    assert adapter._thread_to_room == {
        "alpha:C1:100.0": "room-alpha",
        "beta:C1:100.0": "room-beta",
    }
    assert adapter._room_to_binding["room-alpha"].app_slug == "alpha"
    assert adapter._room_to_binding["room-beta"].app_slug == "beta"


@pytest.mark.asyncio
async def test_concurrent_events_same_thread_create_one_room():
    """Two simultaneous events for one thread must not create duplicate rooms.

    Each Slack event runs in its own background task. Without per-thread
    serialisation both could miss ``_thread_to_room`` and create a room.
    A gated ``create_agent_chat`` forces the overlap deterministically.
    """
    adapter, inner, _, rest = _make_adapter(room_ids=["room-1", "room-2"])
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    gate = asyncio.Event()
    create_calls = {"n": 0}

    async def gated_create(*, chat, **_kwargs):
        create_calls["n"] += 1
        # Block the first creator inside the critical section so a second
        # task (if not serialised) would race in behind it.
        await gate.wait()
        return SimpleNamespace(data=SimpleNamespace(id=f"room-{create_calls['n']}"))

    rest.agent_api_chats.create_agent_chat = AsyncMock(side_effect=gated_create)

    # Fire two events for the same channel/thread directly into the handler
    # so both background coroutines are in flight at once.
    t1 = asyncio.create_task(
        adapter._invoke_brain_for_slack_event(
            app,
            _mention_event(channel="C1", ts="100.0", thread_ts="100.0", text="a")[
                "event"
            ],
        )
    )
    t2 = asyncio.create_task(
        adapter._invoke_brain_for_slack_event(
            app,
            _mention_event(channel="C1", ts="101.0", thread_ts="100.0", text="b")[
                "event"
            ],
        )
    )
    await asyncio.sleep(0)  # let both reach the lock / create call
    gate.set()
    await asyncio.gather(t1, t2)

    # The lock collapses both events onto a single room creation.
    assert rest.agent_api_chats.create_agent_chat.await_count == 1
    assert list(adapter._thread_to_room.values()) == ["room-1"]


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


# ── Band WS path (on_message delegation) ─────────────────────────────────


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
    assert "dev:C1:100.0" not in adapter._thread_to_room
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
    # Crucially: nothing posted to Band REST.
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
    # The standard Band tools are still present (sanity check).
    assert "band_send_message" in names


def test_get_openai_tool_schemas_includes_slack_send_message():
    tools, _, _ = _make_tee_tools()
    schemas = tools.get_openai_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert SLACK_SEND_MESSAGE_TOOL_NAME in names
    assert "band_send_message" in names


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
    """Tools other than ``slack_send_message`` flow through to AgentTools.

    The tee derives task success/failure from the structured outcome, so
    it delegates via ``execute_tool_call_structured`` and returns its
    ``value``.
    """
    from unittest.mock import patch

    from band.runtime.tools import ToolCallOutcome

    tools, _, _ = _make_tee_tools()
    super_mock = AsyncMock(return_value=ToolCallOutcome(value="ok", ok=True))
    with patch.object(AgentTools, "execute_tool_call_structured", super_mock):
        result = await tools.execute_tool_call("band_lookup_peers", {})

    assert result == "ok"
    super_mock.assert_awaited_once_with("band_lookup_peers", {})


@pytest.mark.asyncio
async def test_send_message_no_longer_overridden_uses_real_band_path():
    """The base AgentTools.send_message behavior is restored (mention required)."""
    from band.core.exceptions import BandToolError

    tools, _, slack = _make_tee_tools()
    # Mentionless message hits the platform's "≥1 mention required" guard.
    with pytest.raises(BandToolError, match="mention is required"):
        await tools.send_message("hi", mentions=None)
    # No Slack tee for band_send_message path.
    slack.chat_postMessage.assert_not_awaited()


# ── Channel-mention coverage + thread history backfill ──────────────────────


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
    The same Band room is reused (no second ``create_agent_chat``)."""
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
    # pattern), but same Band room.
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
    # Only one Band room created across both mentions.
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
async def test_bridge_progress_blocks_excluded_from_thread_history():
    """The bridge's own Block Kit plan/status messages must not pollute history.

    Progress messages are posted with ``blocks`` and a placeholder
    fallback ("Working on it…"/"Done"). They carry ``bot_id`` like a real
    reply, so without filtering they'd be re-ingested as fake assistant
    turns on every follow-up. Plain-text bot replies must still survive.
    """
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    web_mocks[app.slug].conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"ts": "100.0", "user": "U001", "text": "hi bot"},
                # Real plain-text reply — keep.
                {
                    "ts": "101.0",
                    "user": "UBOT",
                    "bot_id": "B999",
                    "text": "hello, how can I help?",
                },
                # Block Kit progress message — drop (has blocks + fallback).
                {
                    "ts": "101.5",
                    "user": "UBOT",
                    "bot_id": "B999",
                    "text": "Done",
                    "blocks": [
                        {"type": "section", "text": {"type": "mrkdwn", "text": "✅"}}
                    ],
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
    # Only the user turn + the plain-text bot reply; the "Done" plan block
    # is excluded.
    contents = [m["content"] for m in history]
    assert contents == ["hi bot", "hello, how can I help?"]
    assert "Done" not in contents


@pytest.mark.asyncio
async def test_foreign_bot_replies_are_external_not_assistant():
    """Another bot/webhook in the thread must not become our assistant turn.

    Only messages from *this* app's bot id (resolved via auth.test, "B999"
    in the fixture) map to assistant history. A different bot id is kept as
    external context with a ``slack-bot:<id>`` sender so it can never be
    mistaken for the bridge's own prior answer.
    """
    brain = _SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter())
    adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    web_mocks[app.slug].conversations_replies = AsyncMock(
        return_value={
            "messages": [
                {"ts": "100.0", "user": "U001", "text": "hi"},
                # A *different* bot than ours (ours is B999).
                {
                    "ts": "101.0",
                    "user": "UOTHER",
                    "bot_id": "B_OTHER",
                    "text": "I am a different bot",
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
    foreign = next(m for m in history if m["content"] == "I am a different bot")
    assert foreign["role"] == "user"
    assert foreign["sender_type"] == "user"
    assert foreign["sender_name"] == "slack-bot:B_OTHER"
    # And it is NOT attributed to this agent.
    assert foreign["sender_name"] != "MyBot"


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


# ── Session rehydration via history ─────────────────────────────────────────


def _slack_bootstrap_task_event(
    *,
    app_slug: str = "dev",
    channel: str = "C1",
    thread_ts: str = "100.0",
    room_id: str = "room-1",
) -> dict[str, Any]:
    return {
        "role": "user",
        "content": "Slack thread context",
        "sender_name": "agent",
        "sender_type": "Agent",
        "message_type": "task",
        "metadata": {
            "slack_app_slug": app_slug,
            "slack_channel_id": channel,
            "slack_thread_ts": thread_ts,
            "slack_user_id": "U1",
            "slack_room_id": room_id,
        },
    }


def _agent_input_with_history(
    *,
    room_id: str,
    raw_history: list[dict[str, Any]],
    bootstrap: bool,
    tools: AgentTools,
) -> AgentInput:
    msg = PlatformMessage(
        id="m-bootstrap",
        room_id=room_id,
        content="peer reply",
        sender_id="peer-x",
        sender_type="peer",
        sender_name="Peer X",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    return AgentInput(
        msg=msg,
        tools=tools,
        history=HistoryProvider(raw=raw_history),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=room_id,
    )


@pytest.mark.asyncio
async def test_on_event_rehydrates_room_binding_on_bootstrap():
    """First WS-delivered message in a previously-bridged room restores state."""
    adapter, inner, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")

    inp = _agent_input_with_history(
        room_id="room-resumed",
        raw_history=[
            _slack_bootstrap_task_event(
                app_slug="dev",
                channel="C42",
                thread_ts="999.5",
                room_id="room-resumed",
            ),
        ],
        bootstrap=True,
        tools=AgentTools(room_id="room-resumed", rest=rest, participants=[]),
    )

    await adapter.on_event(inp)

    assert adapter._thread_to_room == {"dev:C42:999.5": "room-resumed"}
    assert adapter._room_to_binding["room-resumed"] == SlackRoomBinding(
        app_slug="dev", channel="C42", thread_ts="999.5"
    )
    # Delegation still happens — the inner brain saw the bootstrap message.
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    assert inner.invocations[0]["room_id"] == "room-resumed"


@pytest.mark.asyncio
async def test_on_event_does_not_rehydrate_when_not_bootstrap():
    """Non-bootstrap messages must never replay history rehydration."""
    adapter, _, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")

    inp = _agent_input_with_history(
        room_id="room-99",
        raw_history=[
            _slack_bootstrap_task_event(
                app_slug="dev", channel="C99", thread_ts="9.0", room_id="room-99"
            ),
        ],
        bootstrap=False,
        tools=AgentTools(room_id="room-99", rest=rest, participants=[]),
    )

    await adapter.on_event(inp)

    assert "C99:9.0" not in adapter._thread_to_room
    assert "room-99" not in adapter._room_to_binding


@pytest.mark.asyncio
async def test_on_event_rehydrate_is_noop_without_slack_context():
    """A bootstrap on a room that has no Slack history must leave state untouched."""
    adapter, _, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")

    inp = _agent_input_with_history(
        room_id="non-slack-room",
        raw_history=[
            {
                "role": "user",
                "content": "hi",
                "sender_name": "alice",
                "sender_type": "user",
                "message_type": "text",
                "metadata": {},
            }
        ],
        bootstrap=True,
        tools=AgentTools(room_id="non-slack-room", rest=rest, participants=[]),
    )

    await adapter.on_event(inp)

    assert adapter._thread_to_room == {}
    assert "non-slack-room" not in adapter._room_to_binding


@pytest.mark.asyncio
async def test_rehydration_then_slack_event_reuses_room_without_new_chat():
    """After rehydration, a Slack event in that thread must NOT create a
    new Band room or emit a new bootstrap context event. Thread
    history is still refetched — that's Slack's recommended pattern and
    is independent of room reuse."""
    adapter, inner, _, rest = _make_adapter(
        inner=_SlackReplyBrain(reply=None, history_converter=_RawHistoryConverter()),
    )
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    # Simulate restart: WS delivers a bootstrap message in an existing
    # Slack-bridged room. Rehydration restores the binding.
    bootstrap_inp = _agent_input_with_history(
        room_id="room-resumed",
        raw_history=[
            _slack_bootstrap_task_event(
                app_slug=app.slug,
                channel="C77",
                thread_ts="500.0",
                room_id="room-resumed",
            ),
        ],
        bootstrap=True,
        tools=AgentTools(room_id="room-resumed", rest=rest, participants=[]),
    )
    await adapter.on_event(bootstrap_inp)

    # Reset Band REST mocks so we can assert no new chat is created.
    rest.agent_api_chats.create_agent_chat.reset_mock()
    rest.agent_api_events.create_agent_chat_event.reset_mock()

    # Slack event lands in the rehydrated thread.
    await _post_slack_event(
        adapter,
        app,
        _mention_event(
            channel="C77",
            ts="600.0",
            thread_ts="500.0",
            text="<@BOT> still there?",
            user="U7",
        ),
    )
    await adapter.wait_idle()

    rest.agent_api_chats.create_agent_chat.assert_not_awaited()
    # No new bootstrap task event (room already exists), but the
    # user-turn mirror still fires — exactly one thought event.
    assert rest.agent_api_events.create_agent_chat_event.await_count == 1
    mirror_evt = rest.agent_api_events.create_agent_chat_event.await_args.kwargs[
        "event"
    ]
    assert mirror_evt.message_type == "thought"
    assert mirror_evt.metadata["slack_mirror"] is True
    # Brain saw the Slack-triggered event in the rehydrated room.
    assert isinstance(inner, _SlackReplyBrain)
    slack_invocations = [i for i in inner.invocations if i["room_id"] == "room-resumed"]
    assert any(inv["msg"].content == "<@BOT> still there?" for inv in slack_invocations)


@pytest.mark.asyncio
async def test_rehydrate_does_not_overwrite_existing_binding():
    """If the in-memory state already has a binding for the room, history
    rehydration must defer to it."""
    adapter, _, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")

    live = SlackRoomBinding(app_slug="dev", channel="C-live", thread_ts="1.0")
    adapter._room_to_binding["room-X"] = live
    adapter._thread_to_room["dev:C-live:1.0"] = "room-X"

    inp = _agent_input_with_history(
        room_id="room-X",
        raw_history=[
            _slack_bootstrap_task_event(
                app_slug="dev",
                channel="C-stale",
                thread_ts="9.0",
                room_id="room-X",
            ),
        ],
        bootstrap=True,
        tools=AgentTools(room_id="room-X", rest=rest, participants=[]),
    )
    await adapter.on_event(inp)

    assert adapter._room_to_binding["room-X"] == live
    assert "dev:C-stale:9.0" not in adapter._thread_to_room


@pytest.mark.asyncio
async def test_context_event_metadata_includes_slack_room_id():
    """The bootstrap task event must carry ``slack_room_id`` so the converter
    can recover the binding from history alone."""
    adapter, _, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter,
        app,
        _mention_event(channel="C123", ts="100.0", text="<@BOT> hi"),
    )
    await adapter.wait_idle()

    # Pick the bootstrap task event specifically — the user-turn mirror
    # (a thought) is also emitted and would otherwise be await_args.
    task_events = [
        c.kwargs["event"]
        for c in rest.agent_api_events.create_agent_chat_event.await_args_list
        if c.kwargs["event"].message_type == "task"
    ]
    assert len(task_events) == 1
    event_arg = task_events[0]
    assert event_arg.metadata["slack_room_id"] == "room-1"
    assert event_arg.metadata["slack_app_slug"] == app.slug
    assert event_arg.metadata["slack_channel_id"] == "C123"
    assert event_arg.metadata["slack_thread_ts"] == "100.0"


# ── Slack context mirroring (audit timeline) ────────────────────────────────


def _thought_events(rest: MagicMock) -> list[Any]:
    return [
        c.kwargs["event"]
        for c in rest.agent_api_events.create_agent_chat_event.await_args_list
        if c.kwargs["event"].message_type == "thought"
    ]


@pytest.mark.asyncio
async def test_user_turn_mirrored_as_thought_with_thread_id():
    """A DM user turn is mirrored as a context-only thought carrying the
    Slack thread id, and no real Band message is posted (no peer loop)."""
    adapter, _, _, rest = _make_adapter(inner=_SlackReplyBrain(reply=None))
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
                "channel": "D999",
                "ts": "1700000000.5",
                "text": "what's the weather?",
                "user": "U42",
            },
        },
    )
    await adapter.wait_idle()

    thoughts = _thought_events(rest)
    assert len(thoughts) == 1
    mirror = thoughts[0]
    assert mirror.message_type == "thought"
    assert mirror.metadata["slack_mirror"] is True
    assert mirror.metadata["slack_thread_ts"] == "1700000000.5"
    assert mirror.metadata["slack_user_id"] == "U42"
    # DM channel resolves to the "DM" label; thread ts stays in metadata.
    assert "1700000000.5" not in mirror.content
    assert "DM" in mirror.content
    assert "what's the weather?" in mirror.content
    # Mirror must NOT post a real message (which would trigger peers/loop).
    rest.agent_api_messages.create_agent_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_mirroring_disabled_emits_no_thought():
    """With mirror_slack_context=False only the bootstrap task event fires."""
    adapter, _, _, rest = _make_adapter(
        inner=_SlackReplyBrain(reply=None),
        mirror_slack_context=False,
    )
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="<@BOT> hi")
    )
    await adapter.wait_idle()

    assert _thought_events(rest) == []
    # Only the bootstrap context (task) event is emitted.
    assert rest.agent_api_events.create_agent_chat_event.await_count == 1
    assert (
        rest.agent_api_events.create_agent_chat_event.await_args.kwargs[
            "event"
        ].message_type
        == "task"
    )


@pytest.mark.asyncio
async def test_mirror_failure_does_not_break_reply():
    """A failing mirror event is swallowed; the brain still replies to Slack."""
    adapter, inner, web_mocks, rest = _make_adapter(
        inner=_SlackReplyBrain(reply="still works"),
    )

    async def fail_only_thought(*, chat_id: str, event: Any, **_kw: Any) -> Any:
        # Bootstrap (task) must succeed so the room is created; only the
        # mirror (thought) blows up.
        if event.message_type == "thought":
            raise RuntimeError("boom")
        return SimpleNamespace(data=SimpleNamespace(id="evt"))

    rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=fail_only_thought
    )
    await adapter.on_started("MyBot", "")
    app = adapter.apps[0]

    await _post_slack_event(
        adapter, app, _mention_event(channel="C1", ts="100.0", text="<@BOT> hi")
    )
    await adapter.wait_idle()

    # Brain ran and replied to Slack despite the mirror (and bootstrap) failing.
    assert isinstance(inner, _SlackReplyBrain)
    assert len(inner.invocations) == 1
    web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
        channel="C1", text="still works", thread_ts="100.0"
    )
