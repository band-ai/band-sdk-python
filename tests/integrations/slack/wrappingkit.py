"""Slack bridge peers, events and wire helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from httpx import ASGITransport

from band.core.simple_adapter import SimpleAdapter
from band.core.types import (
    AgentInput,
    Capability,
    Emit,
    HistoryProvider,
    MessageType,
    PlatformMessage,
)
from band.integrations.slack.adapter import (
    SlackAdapter,
    SlackAdapterConfig,
    SlackTeeingTools,
)
from band.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from band.integrations.slack.types import SlackApp, SlackRoomBinding
from band.runtime.tools import AgentTools
from band.testing.platform import platform_connection_stub

FAKE_CONTROL_COMMAND = "/approve"


class RawHistoryConverter:
    """Identity converter — returns the raw history list unchanged.

    Lets tests inspect exactly what the SlackAdapter synthesized for the
    brain before any framework-specific reshaping.
    """

    def convert(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(raw)


class SlackReplyBrain(SimpleAdapter[Any]):
    """Brain that replies to Slack via the new ``slack_send_message`` tool.

    Calls ``await tools.slack_send_message(reply)`` directly to mimic how
    a real framework adapter would dispatch the tool the LLM picked; with
    ``reply=None`` it declines the turn instead.
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
        if self.reply is None:
            await tools.no_reply("nothing to add")
        elif hasattr(tools, "slack_send_message"):
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
    mirror_slack_context: bool = True,
    **features: Any,
) -> tuple[
    SlackAdapter,
    SlackReplyBrain | SimpleAdapter[Any],
    dict[str, AsyncMock],
    MagicMock,
]:
    inner = inner or SlackReplyBrain()
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
        SlackAdapterConfig(apps=tuple(apps), mirror_slack_context=mirror_slack_context),
        inner=inner,
        rest_client=rest,
        web_client_factory=lambda a: web_mocks[a.slug],
        **features,
    )
    adapter.platform = platform_connection_stub(agent_id=bridge_agent_id)
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


class EmitBrain(SlackReplyBrain):
    """Inner brain that declares (and is configured to use) execution emit."""

    SUPPORTED_EMIT = frozenset({Emit.TOOL_CALLS})
    SUPPORTED_CAPABILITIES = frozenset({Capability.MEMORY})


def _make_band_tools() -> AgentTools:
    rest = MagicMock()
    rest.agent_api_events.create_agent_chat_event = AsyncMock()
    rest.agent_api_messages.create_agent_chat_message = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="m"))
    )
    return AgentTools(room_id="r1", rest=rest, participants=[])


def _make_tee_tools(
    slack: AsyncMock | None = None,
    *,
    wrap: AgentTools | None = None,
) -> tuple[SlackTeeingTools, MagicMock, AsyncMock]:
    base = wrap or _make_band_tools()
    rest = base.rest
    if slack is None:
        # Only set defaults when constructing a fresh mock; a caller-provided
        # ``slack`` may have custom side_effects we mustn't overwrite.
        slack = AsyncMock()
        slack.chat_postMessage = AsyncMock(return_value={"ok": True})
    tools = SlackTeeingTools(
        wrap=base,
        slack=slack,
        binding=SlackRoomBinding(app_slug="dev", channel="C", thread_ts="1.0"),
    )
    return tools, rest, slack


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
        created_at=datetime.now(UTC),
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


class IdleBrain(SlackReplyBrain):
    """A brain whose turn does nothing at all."""

    def __init__(self, *, judges_turns: bool) -> None:
        super().__init__()
        self._judges_turns = judges_turns

    @property
    def judges_turns(self) -> bool:
        return self._judges_turns

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        return None


class CommandBrain(SlackReplyBrain):
    """A brain with one recognizable control command."""

    def is_control_message(self, msg: PlatformMessage) -> bool:
        return msg.content.startswith(FAKE_CONTROL_COMMAND)


def _posted_events(rest: MagicMock, message_type: MessageType) -> list[Any]:
    return [
        c.kwargs["event"]
        for c in rest.agent_api_events.create_agent_chat_event.await_args_list
        if c.kwargs["event"].message_type == message_type
    ]
