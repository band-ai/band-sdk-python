"""Tests for Step 6 — Slack assistant-pane status indicators.

The adapter calls ``assistant.threads.setStatus("is thinking…")`` before
invoking the brain and clears the status (``""``) afterwards. The
``setStatus`` API is part of Slack's Agents & AI Apps surface; in
non-assistant contexts (regular channels) Slack returns an error which
we swallow at DEBUG so the bot still works.
"""

from __future__ import annotations

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
from thenvoi.integrations.slack.adapter import STATUS_THINKING, SlackAdapter
from thenvoi.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from thenvoi.integrations.slack.types import SlackApp
from thenvoi.runtime.tools import AgentTools


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"{SLACK_SIGNATURE_VERSION}={digest}"


def _app(slug: str = "dev") -> SlackApp:
    return SlackApp(slug=slug, signing_secret="test-secret", bot_token=f"xoxb-{slug}")


def _make_rest_mock(room_id: str = "room-1") -> MagicMock:
    rest = MagicMock()
    rest.agent_api_chats.create_agent_chat = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id=room_id))
    )
    rest.agent_api_events.create_agent_chat_event = AsyncMock()
    rest.agent_api_messages.create_agent_chat_message = AsyncMock()
    return rest


class _ReplyingBrain(SimpleAdapter[Any]):
    def __init__(self, reply: str = "answer") -> None:
        super().__init__(history_converter=None)
        self.reply = reply
        self.invocations = 0

    async def on_message(
        self, msg, tools, history, p, c, *, is_session_bootstrap, room_id
    ):
        self.invocations += 1
        if self.reply and hasattr(tools, "slack_send_message"):
            await tools.slack_send_message(self.reply)

    async def on_cleanup(self, room_id: str) -> None:
        return None


class _CrashingBrain(SimpleAdapter[Any]):
    def __init__(self) -> None:
        super().__init__(history_converter=None)

    async def on_message(
        self, msg, tools, history, p, c, *, is_session_bootstrap, room_id
    ):
        raise RuntimeError("brain crashed")

    async def on_cleanup(self, room_id: str) -> None:
        return None


def _make_adapter(
    *,
    inner: SimpleAdapter[Any] | None = None,
    slack_client: AsyncMock | None = None,
) -> tuple[SlackAdapter, AsyncMock, MagicMock]:
    inner = inner or _ReplyingBrain()
    if slack_client is None:
        # Build a fresh client with sensible defaults. If the caller
        # passes their own client (with custom side_effects), we keep it
        # untouched so the test's mock behavior survives.
        slack_client = AsyncMock()
        slack_client.chat_postMessage = AsyncMock(return_value={"ok": True})
        slack_client.assistant_threads_setStatus = AsyncMock(return_value={"ok": True})

    rest = _make_rest_mock()
    adapter = SlackAdapter(
        inner=inner,
        apps=[_app()],
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda _a: slack_client,
    )
    adapter._thenvoi_agent_id = "bridge-uuid"  # type: ignore[attr-defined]
    return adapter, slack_client, rest


async def _post_slack_event(
    adapter: SlackAdapter,
    app: SlackApp,
    payload: dict[str, Any],
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
    channel: str = "C123",
    ts: str = "1700000000.000100",
    text: str = "<@U> hi",
) -> dict[str, Any]:
    return {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "channel": channel,
            "ts": ts,
            "text": text,
            "user": "U999",
        },
    }


# ── Slack webhook path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_event_sets_thinking_status_then_clears_it():
    adapter, slack, _ = _make_adapter()
    await adapter.on_started("Bot", "")
    app = adapter.apps[0]

    await _post_slack_event(adapter, app, _mention_event())
    await adapter.wait_idle()

    calls = slack.assistant_threads_setStatus.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs == {
        "channel_id": "C123",
        "thread_ts": "1700000000.000100",
        "status": STATUS_THINKING,
    }
    assert calls[1].kwargs == {
        "channel_id": "C123",
        "thread_ts": "1700000000.000100",
        "status": "",
    }


@pytest.mark.asyncio
async def test_status_thinking_is_set_before_brain_invocation():
    """The status must be set *before* the brain runs (so the user sees
    'thinking…' during the slow LLM call), and cleared after."""
    timeline: list[str] = []

    class _RecordingBrain(SimpleAdapter[Any]):
        def __init__(self) -> None:
            super().__init__(history_converter=None)

        async def on_message(self, *args: Any, **kwargs: Any) -> None:
            timeline.append("brain_invoked")

        async def on_cleanup(self, room_id: str) -> None:
            return None

    slack = AsyncMock()

    async def record_set(*, channel_id, thread_ts, status):
        timeline.append(f"setStatus={status!r}")
        return {"ok": True}

    slack.assistant_threads_setStatus = AsyncMock(side_effect=record_set)
    slack.chat_postMessage = AsyncMock(return_value={"ok": True})

    adapter, _, _ = _make_adapter(inner=_RecordingBrain(), slack_client=slack)
    await adapter.on_started("Bot", "")
    app = adapter.apps[0]

    await _post_slack_event(adapter, app, _mention_event())
    await adapter.wait_idle()

    assert timeline == [
        f"setStatus={STATUS_THINKING!r}",
        "brain_invoked",
        "setStatus=''",
    ]


@pytest.mark.asyncio
async def test_status_is_cleared_even_when_brain_raises():
    adapter, slack, _ = _make_adapter(inner=_CrashingBrain())
    await adapter.on_started("Bot", "")
    app = adapter.apps[0]

    await _post_slack_event(adapter, app, _mention_event())
    await adapter.wait_idle()

    # Two setStatus calls: open + clear, even though the brain crashed.
    statuses = [
        c.kwargs["status"] for c in slack.assistant_threads_setStatus.await_args_list
    ]
    assert statuses == [STATUS_THINKING, ""]


@pytest.mark.asyncio
async def test_setstatus_failure_is_swallowed():
    """setStatus 404/403 (non-assistant channel, missing scope) is logged
    and ignored — the brain still runs."""
    slack = AsyncMock()
    slack.assistant_threads_setStatus = AsyncMock(
        side_effect=RuntimeError("not_an_assistant_thread")
    )
    slack.chat_postMessage = AsyncMock(return_value={"ok": True})

    brain = _ReplyingBrain(reply="still works")
    adapter, _, rest = _make_adapter(inner=brain, slack_client=slack)
    await adapter.on_started("Bot", "")
    app = adapter.apps[0]

    response = await _post_slack_event(adapter, app, _mention_event())
    await adapter.wait_idle()

    assert response.status_code == 200
    assert brain.invocations == 1
    # The brain still posted its reply via chat.postMessage.
    slack.chat_postMessage.assert_awaited_once()


# ── Thenvoi WS path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ws_message_in_bound_room_also_sets_and_clears_status():
    adapter, slack, rest = _make_adapter()
    await adapter.on_started("Bot", "")
    app = adapter.apps[0]

    # Seed a bound room via the Slack webhook path.
    await _post_slack_event(adapter, app, _mention_event(channel="C1", ts="100.0"))
    await adapter.wait_idle()
    slack.assistant_threads_setStatus.reset_mock()
    slack.chat_postMessage.reset_mock()

    # Now simulate a WS-delivered peer message in the same room.
    real_tools = AgentTools(room_id="room-1", rest=rest, participants=[])
    msg = PlatformMessage(
        id="m2",
        room_id="room-1",
        content="peer joined and asked something",
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

    statuses = [
        c.kwargs["status"] for c in slack.assistant_threads_setStatus.await_args_list
    ]
    assert statuses == [STATUS_THINKING, ""]


@pytest.mark.asyncio
async def test_ws_message_in_unbound_room_does_not_touch_status():
    """Status indicator is only meaningful for Slack-mirrored rooms."""
    # Non-replying brain so we don't trip AgentTools' mention validator
    # on the unbound-room path.
    adapter, slack, rest = _make_adapter(inner=_ReplyingBrain(reply=""))
    await adapter.on_started("Bot", "")

    real_tools = AgentTools(room_id="unrelated-room", rest=MagicMock(), participants=[])
    msg = PlatformMessage(
        id="m1",
        room_id="unrelated-room",
        content="hi",
        sender_id="peer",
        sender_type="peer",
        sender_name="Peer",
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
        room_id="unrelated-room",
    )

    slack.assistant_threads_setStatus.assert_not_awaited()
