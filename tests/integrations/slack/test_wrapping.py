"""Tests for the wrapping-shape SlackAdapter.

Architecture under test:

- ``SlackAdapter`` wraps an ``inner`` framework adapter (the brain).
- The brain sees two outbound tools when the room is Slack-bound:
  - ``band_send_message`` — real Band message (requires mentions)
  - ``slack_send_message`` — posts to the bound Slack thread, Slack-only
- A Slack event → adapter creates/finds a Band room → synthesizes a
  ``PlatformMessage`` → invokes ``inner.on_message`` with the new
  ``SlackTeeingTools`` and a Slack-context note via ``participants_msg``.
- No event mirroring of inbound Slack messages or brain replies. The
  Band room stays empty unless the brain decides to delegate to a peer
  via ``band_send_message``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import warnings
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.core.exceptions import BandToolError
from band.core.protocols import TurnResultAlreadyReported
from band.core.simple_adapter import SimpleAdapter
from band.core.turn_lifecycle import bind_turn_task
from band.core.types import (
    AdapterFeatures,
    Capability,
    Emit,
    MessageType,
    PlatformMessage,
)
from band.integrations.slack.adapter import (
    SLACK_CONTEXT_NOTE,
    SLACK_SEND_MESSAGE_TOOL_NAME,
    SlackAdapter,
    SlackAdapterConfig,
    SlackTeeingTools,
)
from band.integrations.slack.types import SlackRoomBinding
from band.runtime.cycle import TurnScope
from band.runtime.tools import AgentTools, ToolCallOutcome
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.integrations.slack.wrappingkit import (
    EmitBrain,
    IdleBrain,
    RawHistoryConverter,
    SlackReplyBrain,
    _agent_input_with_history,
    _make_adapter,
    _make_band_tools,
    _make_tee_tools,
    _mention_event,
    _post_slack_event,
    _posted_events,
    _slack_app,
    _slack_bootstrap_task_event,
)

# ── Test doubles ─────────────────────────────────────────────────────────────


# ── on_started ──────────────────────────────────────────────────────────────


class TestSlackWrapping:
    @pytest.mark.asyncio
    async def test_on_started_propagates_to_inner_and_sets_agent_id(self):
        adapter, inner, _, _ = _make_adapter()
        assert isinstance(inner, SlackReplyBrain)

        await adapter.on_started("MyBot", "describes me")

        assert inner.started == ("MyBot", "describes me")
        assert inner.platform is not None
        assert inner.platform.agent_id == "bridge-uuid"

    @pytest.mark.asyncio
    async def test_on_started_requires_platform_when_no_rest_client_injected(self):
        inner = SlackReplyBrain()
        adapter = SlackAdapter(SlackAdapterConfig(apps=(_slack_app(),)), inner=inner)
        with pytest.raises(RuntimeError, match="platform connection"):
            await adapter.on_started("MyBot", "")

    @pytest.mark.asyncio
    async def test_on_started_mirrors_inner_support_no_spurious_warning(self, caplog):
        """SlackAdapter must not warn that it 'does not support' what the brain does.

        We adopt ``inner.features`` and delegate reasoning to the inner, so the
        base feature-mismatch check has to run against the inner's declared
        support — not the wrapper's empty defaults.
        """
        inner = EmitBrain(reply=None)
        inner.features = AdapterFeatures(
            emit=frozenset({Emit.TOOL_CALLS}),
            capabilities=frozenset({Capability.MEMORY}),
        )
        adapter, _, _, _ = _make_adapter(inner=inner)

        with caplog.at_level("WARNING"), warnings.catch_warnings():
            # A spurious UserWarning here would mean the wrapper failed to
            # mirror the inner's support before the base check ran.
            warnings.simplefilter("error", UserWarning)
            await adapter.on_started("MyBot", "")

        # Wrapper now reflects the inner's declared support.
        assert adapter.SUPPORTED_EMIT == frozenset({Emit.TOOL_CALLS})
        assert adapter.SUPPORTED_CAPABILITIES == frozenset({Capability.MEMORY})
        # No misleading "does not support" warning for values the brain handles.
        assert not any("does not support" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_explicit_feature_override_reaches_inner(self):
        """An explicit emit=/capabilities= kwarg must govern the actual turn.

        A turn dispatches straight to ``inner.on_message(...)``, whose body reads
        the inner instance's own ``self.features`` — not the wrapper's. Without
        propagating the override onto ``inner.features``, passing
        ``capabilities=`` to ``SlackAdapter`` would silently do nothing.
        """
        inner = EmitBrain(reply=None)
        adapter, _, _, _ = _make_adapter(inner=inner, capabilities=Capability.MEMORY)

        assert adapter.features.capabilities == frozenset({Capability.MEMORY})
        assert inner.features.capabilities == frozenset({Capability.MEMORY})

    def test_partial_feature_override_merges_over_inner_features(self):
        """A partial override must not reset the inner's unrelated narrowing.

        Adding only ``capabilities=`` must keep the inner's explicit ``emit=()``
        silence and its tool filters — merging over ``inner.features``, not
        re-deriving unsupplied fields from defaults (which would resurrect every
        supported emission).
        """
        inner = EmitBrain(reply=None)
        inner.features = AdapterFeatures(
            emit=(),
            include_tools=("band_send_message",),
        )
        adapter, _, _, _ = _make_adapter(inner=inner, capabilities=Capability.MEMORY)

        assert inner.features.capabilities == frozenset({Capability.MEMORY})
        assert inner.features.emit == frozenset()
        assert inner.features.include_tools == ("band_send_message",)
        assert adapter.features == inner.features

    def test_apply_effective_features_reaches_inner(self):
        """Post-construction pruning (Agent.start()'s capability negotiation) must
        reach the inner adapter too, not just the wrapper's own attribute.

        ``_resolve_features`` only mirrors into ``inner.features`` once, at
        construction -- a Slack-wrapped adapter is exactly the shape a real
        capability-negotiation prune runs against, so this has to keep working
        after construction, not just at it.
        """
        inner = EmitBrain(reply=None)
        adapter, _, _, _ = _make_adapter(inner=inner, capabilities=Capability.MEMORY)

        adapter.apply_effective_features(AdapterFeatures())

        assert adapter.features.capabilities == frozenset()
        assert inner.features.capabilities == frozenset()

    @pytest.mark.asyncio
    async def test_slack_event_creates_room_invokes_brain_and_replies_via_tool(self):
        adapter, inner, web_mocks, rest = _make_adapter(
            inner=SlackReplyBrain(reply="Hello, world."),
            room_ids=["room-1"],
        )
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        inv = inner.invocations[0]
        assert inv["msg"].content == "<@U001> ping"
        assert inv["msg"].sender_id == "slack:U999"
        assert inv["msg"].metadata["slack_channel_id"] == "C123"
        assert inv["participants_msg"] == SLACK_CONTEXT_NOTE
        assert inv["is_session_bootstrap"] is True
        # Tools are the teeing subclass.
        assert isinstance(inv["tools"], SlackTeeingTools)

        # Brain's reply went to Slack only.
        web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
            channel="C123",
            text="Hello, world.",
            thread_ts="1700000000.000100",
        )

    @pytest.mark.asyncio
    async def test_second_event_in_same_thread_reuses_room(self):
        adapter, inner, _, rest = _make_adapter(room_ids=["room-1", "room-2"])
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="first")
        )
        await adapter.wait_idle()
        await _post_slack_event(
            adapter,
            app,
            _mention_event(
                channel="C1", ts="200.0", thread_ts="100.0", text="follow-up"
            ),
        )
        await adapter.wait_idle()

        assert rest.agent_api_chats.create_agent_chat.await_count == 1
        assert isinstance(inner, SlackReplyBrain)
        assert inner.invocations[0]["is_session_bootstrap"] is True
        assert inner.invocations[1]["is_session_bootstrap"] is False

    @pytest.mark.asyncio
    async def test_same_thread_tuple_across_apps_maps_to_distinct_rooms(self):
        """Two SlackApps sharing a channel/thread tuple must get separate rooms.

        The room lookup key includes ``app.slug``, so a workspace-scoped
        ``channel:thread_ts`` collision between two apps can't cross-route
        replies into the wrong app/workspace.
        """
        apps = [_slack_app("alpha"), _slack_app("beta")]
        adapter, _inner, _, rest = _make_adapter(
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
    async def test_concurrent_events_same_thread_create_one_room(self):
        """Two simultaneous events for one thread must not create duplicate rooms.

        Each Slack event runs in its own background task. Without per-thread
        serialisation both could miss ``_thread_to_room`` and create a room.
        A gated ``create_agent_chat`` forces the overlap deterministically.
        """
        adapter, _inner, _, rest = _make_adapter(room_ids=["room-1", "room-2"])
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
    async def test_dm_event_creates_room_and_invokes_brain(self):
        adapter, inner, _, rest = _make_adapter()
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        assert inner.invocations[0]["msg"].content == "ping"

    @pytest.mark.asyncio
    async def test_bot_authored_messages_are_ignored(self):
        adapter, inner, _, rest = _make_adapter()
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert inner.invocations == []

    @pytest.mark.asyncio
    async def test_unsupported_event_type_is_ignored(self):
        adapter, inner, _, rest = _make_adapter()
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert inner.invocations == []

    @pytest.mark.asyncio
    async def test_on_message_delegates_to_inner_for_unbound_room(self):
        """A peer-to-peer message in a room we never bound to Slack."""
        adapter, inner, _, _ = _make_adapter(inner=SlackReplyBrain(reply=None))
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
            created_at=datetime.now(UTC),
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

        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        # Unbound: raw tools, no Slack context note.
        assert not isinstance(inner.invocations[0]["tools"], SlackTeeingTools)
        assert inner.invocations[0]["participants_msg"] is None

    @pytest.mark.asyncio
    async def test_on_message_wraps_tools_and_injects_note_for_bound_room(self):
        """When a peer messages in a Slack-bound room, brain gets the tee + note."""
        adapter, inner, web_mocks, rest = _make_adapter()
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        # Seed a Slack-bound room.
        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="initial")
        )
        await adapter.wait_idle()
        assert isinstance(inner, SlackReplyBrain)
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
            created_at=datetime.now(UTC),
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
        assert isinstance(inv["tools"], SlackTeeingTools)
        assert inv["participants_msg"] == SLACK_CONTEXT_NOTE
        # Brain's reply ('Here is the answer.') went to Slack.
        web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
            channel="C1", text="Here is the answer.", thread_ts="100.0"
        )

    @pytest.mark.asyncio
    async def test_on_message_merges_existing_participants_msg_with_context_note(self):
        """Caller-provided participants_msg is preserved when we inject our note."""
        adapter, inner, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="hi")
        )
        await adapter.wait_idle()
        assert isinstance(inner, SlackReplyBrain)
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
            created_at=datetime.now(UTC),
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

    @pytest.mark.asyncio
    async def test_on_cleanup_drops_binding_and_calls_inner(self):
        adapter, inner, _, _ = _make_adapter()
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        await _post_slack_event(adapter, app, _mention_event(channel="C1", ts="100.0"))
        await adapter.wait_idle()
        assert "room-1" in adapter._room_to_binding

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._room_to_binding
        assert "dev:C1:100.0" not in adapter._thread_to_room
        assert isinstance(inner, SlackReplyBrain)
        assert inner.cleaned_up == ["room-1"]

    @pytest.mark.asyncio
    async def test_ack_returns_before_brain_finishes(self):
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
        app = adapter.config.apps[0]

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
    async def test_brain_exception_does_not_break_subsequent_events(self):
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
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="first")
        )
        await adapter.wait_idle()
        await _post_slack_event(
            adapter, app, _mention_event(channel="C2", ts="200.0", text="second")
        )
        await adapter.wait_idle()

        assert calls == ["first", "second"]

    @pytest.mark.asyncio
    async def test_slack_send_message_posts_to_slack_only(self):
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
    async def test_slack_send_message_returns_error_dict_on_failure(self):
        slack = AsyncMock()
        slack.chat_postMessage = AsyncMock(side_effect=RuntimeError("kaboom"))
        tools, _, _ = _make_tee_tools(slack=slack)

        result = await tools.slack_send_message("hello")
        assert result["ok"] is False
        assert "kaboom" in result["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("chat_post_message", "replied"),
        [
            pytest.param(AsyncMock(return_value={"ok": True}), True, id="posted"),
            pytest.param(
                AsyncMock(side_effect=RuntimeError("kaboom")), False, id="failed"
            ),
        ],
    )
    async def test_slack_reply_settles_the_wrapped_turn_only_when_posted(
        self, chat_post_message: AsyncMock, replied: bool
    ):
        """The adapter judges the turn on the tools it handed out, not the tee."""
        slack = AsyncMock()
        slack.chat_postMessage = chat_post_message
        band_tools = _make_band_tools()
        tools, _, _ = _make_tee_tools(slack=slack, wrap=band_tools)

        await tools.execute_tool_call(SLACK_SEND_MESSAGE_TOOL_NAME, {"content": "hi"})

        assert band_tools.turn.replied is replied

    def test_get_anthropic_tool_schemas_includes_slack_send_message(self):
        tools, _, _ = _make_tee_tools()
        schemas = tools.get_anthropic_tool_schemas()
        names = [s["name"] for s in schemas]
        assert SLACK_SEND_MESSAGE_TOOL_NAME in names
        # The standard Band tools are still present (sanity check).
        assert "band_send_message" in names

    def test_get_openai_tool_schemas_includes_slack_send_message(self):
        tools, _, _ = _make_tee_tools()
        schemas = tools.get_openai_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert SLACK_SEND_MESSAGE_TOOL_NAME in names
        assert "band_send_message" in names

    def test_base_agent_tools_does_not_include_slack_tool(self):
        """The slack tool is only exposed via the teeing subclass."""
        rest = MagicMock()
        base = AgentTools(room_id="r", rest=rest, participants=[])
        schemas = base.get_anthropic_tool_schemas()
        names = [s["name"] for s in schemas]
        assert SLACK_SEND_MESSAGE_TOOL_NAME not in names

    @pytest.mark.asyncio
    async def test_execute_tool_call_routes_slack_send_message(self):
        tools, _, slack = _make_tee_tools()
        result = await tools.execute_tool_call(
            SLACK_SEND_MESSAGE_TOOL_NAME, {"content": "hi from llm"}
        )
        assert result == {"ok": True}
        slack.chat_postMessage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_tool_call_rejects_empty_content(self):
        tools, _, slack = _make_tee_tools()
        result = await tools.execute_tool_call(
            SLACK_SEND_MESSAGE_TOOL_NAME, {"content": ""}
        )
        assert isinstance(result, str)
        assert "requires a non-empty 'content'" in result
        slack.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_tool_call_delegates_non_slack_tools_to_super(self):
        """Tools other than ``slack_send_message`` flow through to AgentTools.

        The tee derives task success/failure from the structured outcome, so
        it delegates via ``execute_tool_call_structured`` and returns its
        ``value``.
        """

        tools, _, _ = _make_tee_tools()
        super_mock = AsyncMock(return_value=ToolCallOutcome(value="ok", ok=True))
        with patch.object(AgentTools, "execute_tool_call_structured", super_mock):
            result = await tools.execute_tool_call("band_lookup_peers", {})

        assert result == "ok"
        super_mock.assert_awaited_once_with("band_lookup_peers", {})

    @pytest.mark.asyncio
    async def test_send_message_no_longer_overridden_uses_real_band_path(self):
        """The base AgentTools.send_message behavior is restored (mention required)."""

        tools, _, slack = _make_tee_tools()
        # Mentionless message hits the platform's "≥1 mention required" guard.
        with pytest.raises(BandToolError, match="mention is required"):
            await tools.send_message("hi", mentions=None)
        # No Slack tee for band_send_message path.
        slack.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_channel_top_level_mention_does_not_fetch_thread_history(self):
        """(a) Top-level @mention in a channel: thread_ts == ts, no backfill."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter,
            app,
            _mention_event(channel="C123", ts="100.0", text="<@U001> hi"),
        )
        await adapter.wait_idle()

        web_mocks[app.slug].conversations_replies.assert_not_awaited()
        assert isinstance(inner, SlackReplyBrain)
        assert inner.invocations[0]["history"] == []

    @pytest.mark.asyncio
    async def test_channel_mention_in_existing_thread_pulls_history(self):
        """(b) @mention inside an existing thread: pull conversations.replies once."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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

        assert isinstance(inner, SlackReplyBrain)
        history = inner.invocations[0]["history"]
        # Trigger excluded; remaining pre-mention turns preserved in order.
        assert [h["content"] for h in history] == [
            "hey team",
            "anyone seen the report?",
        ]
        assert all(h["role"] == "user" for h in history)
        assert all(h["sender_type"] == "user" for h in history)
        assert [h["sender_name"] for h in history] == ["slack:U001", "slack:U002"]

    @pytest.mark.asyncio
    async def test_subsequent_mention_in_same_thread_refetches_history(self):
        """(c) Slack does not deliver thread context with each event; the adapter
        must refetch on every threaded mention so stateless brains see the
        growing conversation. Slack's own Bolt JS reference does the same.
        The same Band room is reused (no second ``create_agent_chat``)."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, _, web_mocks, rest = _make_adapter(
            inner=brain,
            room_ids=["room-1", "room-2"],
        )
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
    async def test_bot_replies_in_thread_history_become_assistant_role(self):
        """Self-replies in the fetched thread are tagged as assistant turns."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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

        assert isinstance(inner, SlackReplyBrain)
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
    async def test_bridge_progress_blocks_excluded_from_thread_history(self):
        """The bridge's own Block Kit plan/status messages must not pollute history.

        Progress messages are posted with ``blocks`` and a placeholder
        fallback ("Working on it…"/"Done"). They carry ``bot_id`` like a real
        reply, so without filtering they'd be re-ingested as fake assistant
        turns on every follow-up. Plain-text bot replies must still survive.
        """
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": "✅"},
                            }
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

        assert isinstance(inner, SlackReplyBrain)
        history = inner.invocations[0]["history"]
        # Only the user turn + the plain-text bot reply; the "Done" plan block
        # is excluded.
        contents = [m["content"] for m in history]
        assert contents == ["hi bot", "hello, how can I help?"]
        assert "Done" not in contents

    @pytest.mark.asyncio
    async def test_foreign_bot_replies_are_external_not_assistant(self):
        """Another bot/webhook in the thread must not become our assistant turn.

        Only messages from *this* app's bot id (resolved via auth.test, "B999"
        in the fixture) map to assistant history. A different bot id is kept as
        external context with a ``slack-bot:<id>`` sender so it can never be
        mistaken for the bridge's own prior answer.
        """
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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

        assert isinstance(inner, SlackReplyBrain)
        history = inner.invocations[0]["history"]
        foreign = next(m for m in history if m["content"] == "I am a different bot")
        assert foreign["role"] == "user"
        assert foreign["sender_type"] == "user"
        assert foreign["sender_name"] == "slack-bot:B_OTHER"
        # And it is NOT attributed to this agent.
        assert foreign["sender_name"] != "MyBot"

    @pytest.mark.asyncio
    async def test_fresh_dm_does_not_fetch_thread_history(self):
        """A brand-new DM (thread_ts == ts, new room) must not call replies."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert inner.invocations[0]["history"] == []

    @pytest.mark.asyncio
    async def test_thread_history_fetch_failure_falls_back_to_empty_history(self):
        """A failed conversations.replies must not break the trigger reply."""
        brain = SlackReplyBrain(reply=None, history_converter=RawHistoryConverter())
        adapter, inner, web_mocks, _ = _make_adapter(inner=brain)
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        assert inner.invocations[0]["history"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("brain_judges_turns", "verdict", "failures"),
        [
            pytest.param(
                True,
                pytest.raises(TurnResultAlreadyReported),
                [MISSING_REPLY_FAILURE],
                id="judged-brain",
            ),
            pytest.param(False, nullcontext(), [], id="exempt-brain"),
        ],
    )
    async def test_an_idle_turn_is_judged_by_the_brains_rule(
        self,
        brain_judges_turns: bool,
        verdict: AbstractContextManager[Any],
        failures: list[tuple[str, str]],
    ):
        adapter, _, _, _ = _make_adapter(
            inner=IdleBrain(judges_turns=brain_judges_turns)
        )
        tools = FakeAgentTools(room_id="room-1")
        inp = _agent_input_with_history(
            room_id="room-1", raw_history=[], bootstrap=False, tools=tools
        )

        with verdict:
            await adapter.on_event(inp)

        assert failure_reports(tools) == failures

    @pytest.mark.asyncio
    async def test_an_idle_slack_originated_turn_is_reported(
        self,
        caplog: pytest.LogCaptureFixture,
    ):
        """A turn that arrives from Slack rather than the platform is judged too,
        and its reported failure is not logged again as a crash."""
        adapter, _, _, rest = _make_adapter(
            inner=IdleBrain(judges_turns=True), room_ids=["room-1"]
        )
        await adapter.on_started("MyBot", "")

        with caplog.at_level(logging.WARNING):
            await _post_slack_event(adapter, adapter.config.apps[0], _mention_event())
            await adapter.wait_idle()

        (error,) = _posted_events(rest, MessageType.ERROR)
        assert error.content == MISSING_REPLY_FAILURE[1]
        assert max(r.levelno for r in caplog.records) == logging.WARNING

    @pytest.mark.asyncio
    async def test_a_slack_turn_whose_report_failed_is_not_logged_as_a_crash(
        self,
        caplog: pytest.LogCaptureFixture,
    ):
        """A Slack turn has no runtime to fall back to; the failed post is logged
        where it failed, and the handler does not log the turn again as a crash."""
        adapter, _, _, rest = _make_adapter(
            inner=IdleBrain(judges_turns=True), room_ids=["room-1"]
        )
        await adapter.on_started("MyBot", "")
        create = rest.agent_api_events.create_agent_chat_event

        async def fail_error_events(**kwargs: Any) -> Any:
            if kwargs["event"].message_type == MessageType.ERROR:
                raise RuntimeError("503")
            return create.return_value

        create.side_effect = fail_error_events

        with caplog.at_level(logging.WARNING):
            await _post_slack_event(adapter, adapter.config.apps[0], _mention_event())
            await adapter.wait_idle()

        assert [
            r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
        ] == ["send_failure could not post the failure event"]

    @pytest.mark.asyncio
    async def test_on_event_rehydrates_room_binding_on_bootstrap(self):
        """First WS-delivered message in a previously-bridged room restores state."""
        adapter, inner, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
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
        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        assert inner.invocations[0]["room_id"] == "room-resumed"

    @pytest.mark.asyncio
    async def test_on_event_does_not_rehydrate_when_not_bootstrap(self):
        """Non-bootstrap messages must never replay history rehydration."""
        adapter, _, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
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
    async def test_on_event_rehydrate_is_noop_without_slack_context(self):
        """A bootstrap on a room that has no Slack history must leave state untouched."""
        adapter, _, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
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
    async def test_rehydration_then_slack_event_reuses_room_without_new_chat(self):
        """After rehydration, a Slack event in that thread must NOT create a
        new Band room or emit a new bootstrap context event. Thread
        history is still refetched — that's Slack's recommended pattern and
        is independent of room reuse."""
        adapter, inner, _, rest = _make_adapter(
            inner=SlackReplyBrain(reply=None, history_converter=RawHistoryConverter()),
        )
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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
        assert isinstance(inner, SlackReplyBrain)
        slack_invocations = [
            i for i in inner.invocations if i["room_id"] == "room-resumed"
        ]
        assert any(
            inv["msg"].content == "<@BOT> still there?" for inv in slack_invocations
        )

    @pytest.mark.asyncio
    async def test_rehydrate_does_not_overwrite_existing_binding(self):
        """If the in-memory state already has a binding for the room, history
        rehydration must defer to it."""
        adapter, _, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
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
    async def test_context_event_metadata_includes_slack_room_id(self):
        """The bootstrap task event must carry ``slack_room_id`` so the converter
        can recover the binding from history alone."""
        adapter, _, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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

    @pytest.mark.asyncio
    async def test_user_turn_mirrored_as_thought_with_thread_id(self):
        """A DM user turn is mirrored as a context-only thought carrying the
        Slack thread id, and no real Band message is posted (no peer loop)."""
        adapter, _, _, rest = _make_adapter(inner=SlackReplyBrain(reply=None))
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

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

        thoughts = _posted_events(rest, MessageType.THOUGHT)
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
    async def test_mirroring_disabled_emits_no_thought(self):
        """With mirror_slack_context=False only the bootstrap task event fires."""
        adapter, _, _, rest = _make_adapter(
            inner=SlackReplyBrain(reply=None),
            mirror_slack_context=False,
        )
        await adapter.on_started("MyBot", "")
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="<@BOT> hi")
        )
        await adapter.wait_idle()

        assert _posted_events(rest, MessageType.THOUGHT) == []
        # Only the bootstrap context (task) event is emitted.
        assert rest.agent_api_events.create_agent_chat_event.await_count == 1
        assert (
            rest.agent_api_events.create_agent_chat_event.await_args.kwargs[
                "event"
            ].message_type
            == "task"
        )

    @pytest.mark.asyncio
    async def test_mirror_failure_does_not_break_reply(self):
        """A failing mirror event is swallowed; the brain still replies to Slack."""
        adapter, inner, web_mocks, rest = _make_adapter(
            inner=SlackReplyBrain(reply="still works"),
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
        app = adapter.config.apps[0]

        await _post_slack_event(
            adapter, app, _mention_event(channel="C1", ts="100.0", text="<@BOT> hi")
        )
        await adapter.wait_idle()

        # Brain ran and replied to Slack despite the mirror (and bootstrap) failing.
        assert isinstance(inner, SlackReplyBrain)
        assert len(inner.invocations) == 1
        web_mocks[app.slug].chat_postMessage.assert_awaited_once_with(
            channel="C1", text="still works", thread_ts="100.0"
        )

    async def test_slack_wrapper_keeps_worker_and_late_posts_in_original_scope(
        self,
    ) -> None:
        base = _make_band_tools()
        base._scope = TurnScope(control_revision=0)
        tools, rest, _ = _make_tee_tools(wrap=base)
        started = asyncio.Event()

        async def work() -> None:
            started.set()
            await asyncio.Future[None]()

        owner = bind_turn_task(tools, asyncio.create_task(work()))
        await started.wait()
        owner.terminate()
        assert await owner.drain()
        with pytest.raises(asyncio.CancelledError):
            await tools.send_event("late reply", "thought")
        rest.agent_api_events.create_agent_chat_event.assert_not_awaited()
