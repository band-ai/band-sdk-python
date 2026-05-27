"""Tests for KoreAIAdapter."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.integrations.koreai.adapter import KoreAIAdapter
from thenvoi.integrations.koreai.callback_server import (
    KoreAICallbackServer,
    _TurnCollector,
)
from thenvoi.integrations.koreai.client import KoreAIClient
from thenvoi.integrations.koreai.template_extractor import extract_text
from thenvoi.integrations.koreai.types import (
    CallbackData,
    KoreAIConfig,
    KoreAISessionState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_CONFIG = KoreAIConfig(
    bot_id="st-test-bot",
    client_id="cs-test-client",
    client_secret="test-secret-key",
    callback_url="https://bridge.example.com/koreai/callback",
)


def _make_mock_server() -> MagicMock:
    """Create a mock callback server with the active_room_lock."""
    mock_server = MagicMock(spec=KoreAICallbackServer)
    mock_server.active_room_lock = asyncio.Lock()
    return mock_server


@pytest.fixture
def config() -> KoreAIConfig:
    return KoreAIConfig(
        bot_id="st-test-bot",
        client_id="cs-test-client",
        client_secret="test-secret-key",
        callback_url="https://bridge.example.com/koreai/callback",
    )


@pytest.fixture
def adapter(config: KoreAIConfig) -> KoreAIAdapter:
    return KoreAIAdapter(config=config)


@pytest.fixture
def mock_tools() -> MagicMock:
    tools = MagicMock()
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools


@pytest.fixture
def sample_msg() -> MagicMock:
    msg = MagicMock()
    msg.content = "check order #123"
    msg.sender_id = "agent-langgraph"
    msg.sender_name = "LangGraph"
    msg.sender_type = "agent"
    msg.room_id = "room-abc"
    return msg


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_valid_config(self, config: KoreAIConfig) -> None:
        assert config.bot_id == "st-test-bot"
        assert config.client_id == "cs-test-client"
        assert config.jwt_algorithm == "HS256"
        assert config.callback_port == 3100
        assert config.response_timeout_seconds == 120
        assert config.session_timeout_seconds == 900

    def test_missing_bot_id_raises(self) -> None:
        with pytest.raises(ValueError, match="bot_id"):
            KoreAIConfig(
                client_id="cs-test",
                client_secret="secret",
                callback_url="https://example.com/callback",
            )

    def test_missing_client_id_raises(self) -> None:
        with pytest.raises(ValueError, match="client_id"):
            KoreAIConfig(
                bot_id="st-test",
                client_secret="secret",
                callback_url="https://example.com/callback",
            )

    def test_missing_client_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="client_secret"):
            KoreAIConfig(
                bot_id="st-test",
                client_id="cs-test",
                callback_url="https://example.com/callback",
            )

    def test_missing_callback_url_raises(self) -> None:
        with pytest.raises(ValueError, match="callback_url"):
            KoreAIConfig(
                bot_id="st-test",
                client_id="cs-test",
                client_secret="secret",
            )

    def test_missing_multiple_fields_lists_all(self) -> None:
        with pytest.raises(
            ValueError, match="bot_id.*client_id.*client_secret.*callback_url"
        ):
            KoreAIConfig()

    def test_invalid_jwt_algorithm_raises(self) -> None:
        with pytest.raises(ValueError, match="jwt_algorithm"):
            KoreAIConfig(
                bot_id="st-test",
                client_id="cs-test",
                client_secret="secret",
                callback_url="https://example.com/callback",
                jwt_algorithm="RS256",
            )

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KOREAI_BOT_ID", "st-from-env")
        monkeypatch.setenv("KOREAI_CLIENT_ID", "cs-from-env")
        monkeypatch.setenv("KOREAI_CLIENT_SECRET", "secret-from-env")
        monkeypatch.setenv("KOREAI_CALLBACK_URL", "https://env.example.com/callback")

        cfg = KoreAIConfig()
        assert cfg.bot_id == "st-from-env"
        assert cfg.client_id == "cs-from-env"
        assert cfg.client_secret == "secret-from-env"
        assert cfg.callback_url == "https://env.example.com/callback"

    def test_constructor_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KOREAI_BOT_ID", "st-from-env")
        monkeypatch.setenv("KOREAI_CLIENT_ID", "cs-from-env")
        monkeypatch.setenv("KOREAI_CLIENT_SECRET", "secret-from-env")
        monkeypatch.setenv("KOREAI_CALLBACK_URL", "https://env.example.com/callback")

        cfg = KoreAIConfig(
            bot_id="st-explicit",
            client_id="cs-explicit",
            client_secret="secret-explicit",
            callback_url="https://explicit.example.com/callback",
        )
        assert cfg.bot_id == "st-explicit"
        assert cfg.client_id == "cs-explicit"


# ---------------------------------------------------------------------------
# Adapter initialization
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_default_initialization(self, adapter: KoreAIAdapter) -> None:
        assert adapter.config.bot_id == "st-test-bot"
        assert adapter.custom_section == ""
        assert adapter._client is None
        assert adapter._callback_server is None
        assert adapter._room_states == {}

    def test_custom_section(self, config: KoreAIConfig) -> None:
        adapter = KoreAIAdapter(config=config, custom_section="Extra info")
        assert adapter.custom_section == "Extra info"


# ---------------------------------------------------------------------------
# on_started
# ---------------------------------------------------------------------------


class TestOnStarted:
    @pytest.mark.asyncio
    async def test_starts_callback_server_and_client(
        self, adapter: KoreAIAdapter
    ) -> None:
        with (
            patch.object(
                KoreAICallbackServer, "start", new_callable=AsyncMock
            ) as mock_server_start,
            patch.object(
                KoreAIClient, "start", new_callable=AsyncMock
            ) as mock_client_start,
        ):
            await adapter.on_started("KoreBot", "A Kore.ai bot")

        mock_server_start.assert_awaited_once()
        mock_client_start.assert_awaited_once()
        assert adapter._callback_server is not None
        assert adapter._client is not None

    @pytest.mark.asyncio
    async def test_warns_experimental_callback_auth(
        self, adapter: KoreAIAdapter, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch.object(KoreAICallbackServer, "start", new_callable=AsyncMock),
            patch.object(KoreAIClient, "start", new_callable=AsyncMock),
        ):
            await adapter.on_started("KoreBot", "A Kore.ai bot")

        assert any("experimental" in r.message.lower() for r in caplog.records)
        assert any(
            "without cryptographic verification" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_rejects_unverified_webhook_secret(self) -> None:
        cfg = KoreAIConfig(
            bot_id="st-test",
            client_id="cs-test",
            client_secret="secret",
            callback_url="https://bridge.example.com/koreai/callback",
            webhook_secret="shared-secret",
        )
        adapter = KoreAIAdapter(config=cfg)

        with pytest.raises(
            ValueError, match="signature verification is not implemented"
        ):
            await adapter.on_started("KoreBot", "A Kore.ai bot")

    @pytest.mark.asyncio
    async def test_warns_http_callback_url(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = KoreAIConfig(
            bot_id="st-test",
            client_id="cs-test",
            client_secret="secret",
            callback_url="http://insecure.example.com/callback",
        )
        adapter = KoreAIAdapter(config=cfg)

        with (
            patch.object(KoreAICallbackServer, "start", new_callable=AsyncMock),
            patch.object(KoreAIClient, "start", new_callable=AsyncMock),
        ):
            await adapter.on_started("KoreBot", "A Kore.ai bot")

        assert any("HTTP" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Message sent to Kore.ai, callback received, response delivered."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=["Your order #123 shipped March 28"],
            task_completed=True,
            end_reason="Fulfilled",
            task_name="Check Order Status",
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        # Verify message was sent to Kore.ai (new_session=False: Kore.ai
        # auto-creates a session for a new from.id per the webhook V2 spec)
        mock_client.send_message.assert_awaited_once_with(
            room_id="room-abc",
            text="check order #123",
            new_session=False,
        )

        # Verify response was delivered to ChatRoom. The adapter passes the
        # sender id through the normal mention resolver instead of the
        # deprecated dict mention shape.
        mock_tools.send_message.assert_awaited_once_with(
            content="Your order #123 shipped March 28",
            mentions=["agent-langgraph"],
        )

        # Verify session state persisted
        task_event_calls = [
            c
            for c in mock_tools.send_event.call_args_list
            if c.kwargs.get("message_type") == "task"
            and c.kwargs.get("metadata", {}).get("koreai_identity")
        ]
        assert len(task_event_calls) == 1

    @pytest.mark.asyncio
    async def test_multi_part_response(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Multiple text callbacks each delivered as separate messages."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=["Part 1: Order found", "Part 2: Shipped on March 28"],
            task_completed=True,
            end_reason="Fulfilled",
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        assert mock_tools.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_agent_transfer(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """endReason=Interrupted produces task event for handoff."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=["Connecting you to a specialist..."],
            task_completed=True,
            end_reason="Interrupted",
            task_name="Customer Support",
            is_agent_transfer=True,
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        # Verify handoff event was sent
        handoff_calls = [
            c
            for c in mock_tools.send_event.call_args_list
            if "handoff" in str(c.kwargs.get("content", ""))
        ]
        assert len(handoff_calls) == 1
        metadata = handoff_calls[0].kwargs["metadata"]
        assert metadata["koreai_end_reason"] == "Interrupted"
        assert metadata["koreai_task_name"] == "Customer Support"

    @pytest.mark.asyncio
    async def test_timeout_sends_error_event(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """No callback within deadline produces error event."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData()  # Empty - no messages, no task completion
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        # Verify error event was sent
        error_calls = [
            c
            for c in mock_tools.send_event.call_args_list
            if c.kwargs.get("message_type") == "error"
        ]
        assert len(error_calls) == 1
        assert "did not respond" in error_calls[0].kwargs["content"]

    @pytest.mark.asyncio
    async def test_bootstrap_rehydration(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Session state restored from history on bootstrap."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=["Response"], task_completed=True, end_reason="Fulfilled"
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        recent_time = time.time() - 60  # 60 seconds ago (within timeout)
        history = KoreAISessionState(
            koreai_identity="room-abc",
            koreai_last_activity=recent_time,
        )

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-abc",
        )

        # Should NOT start a new session (session still active)
        mock_client.send_message.assert_awaited_once()
        call_kwargs = mock_client.send_message.call_args.kwargs
        assert call_kwargs["new_session"] is False

    @pytest.mark.asyncio
    async def test_session_expired_sends_new_session(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Expired session sends session.new=true."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=["Response"], task_completed=True, end_reason="Fulfilled"
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        old_time = time.time() - 2000  # Well past 900s timeout
        history = KoreAISessionState(
            koreai_identity="room-abc",
            koreai_last_activity=old_time,
        )

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-abc",
        )

        call_kwargs = mock_client.send_message.call_args.kwargs
        assert call_kwargs["new_session"] is True

    @pytest.mark.asyncio
    async def test_not_started_drops_message(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Message dropped if adapter not started."""
        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        mock_tools.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_task_fulfilled_not_sent_as_message(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """Fulfilled task completion is logged, not sent as chat message."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        collector = MagicMock(spec=_TurnCollector)
        callback_data = CallbackData(
            messages=[],
            task_completed=True,
            end_reason="Fulfilled",
            task_name="Show Balance",
        )
        collector.wait_for_messages = AsyncMock(return_value=callback_data)
        mock_server.register_turn.return_value = collector

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        # No chat message sent
        mock_tools.send_message.assert_not_awaited()
        # No error event either (task completed fine, just no text)
        error_calls = [
            c
            for c in mock_tools.send_event.call_args_list
            if c.kwargs.get("message_type") == "error"
        ]
        assert len(error_calls) == 0

    @pytest.mark.asyncio
    async def test_client_error_sends_error_event(
        self,
        adapter: KoreAIAdapter,
        mock_tools: MagicMock,
        sample_msg: MagicMock,
    ) -> None:
        """HTTP client error surfaces as error event."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_client.send_message.side_effect = RuntimeError("Connection refused")
        mock_server = _make_mock_server()
        mock_server.register_turn.return_value = MagicMock(spec=_TurnCollector)

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_message(
            msg=sample_msg,
            tools=mock_tools,
            history=KoreAISessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-abc",
        )

        error_calls = [
            c
            for c in mock_tools.send_event.call_args_list
            if c.kwargs.get("message_type") == "error"
        ]
        assert len(error_calls) == 1
        assert "Connection refused" in error_calls[0].kwargs["content"]


# ---------------------------------------------------------------------------
# on_cleanup
# ---------------------------------------------------------------------------


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_sends_session_closure(self, adapter: KoreAIAdapter) -> None:
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        adapter._client = mock_client
        adapter._callback_server = mock_server
        adapter._room_states["room-abc"] = MagicMock()
        adapter._room_locks["room-abc"] = asyncio.Lock()

        await adapter.on_cleanup("room-abc")

        mock_client.close_session.assert_awaited_once_with("room-abc")
        assert "room-abc" not in adapter._room_states
        assert "room-abc" not in adapter._room_locks

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self, adapter: KoreAIAdapter) -> None:
        """Calling cleanup twice doesn't raise."""
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        adapter._client = mock_client
        adapter._callback_server = mock_server

        await adapter.on_cleanup("room-abc")
        await adapter.on_cleanup("room-abc")

        # close_session not called since room wasn't in _room_states
        mock_client.close_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cleanup_unregisters_turn(self, adapter: KoreAIAdapter) -> None:
        mock_client = AsyncMock(spec=KoreAIClient)
        mock_server = _make_mock_server()

        adapter._client = mock_client
        adapter._callback_server = mock_server
        adapter._room_states["room-abc"] = MagicMock()

        await adapter.on_cleanup("room-abc")

        mock_server.unregister_turn.assert_called_once_with("room-abc")

    @pytest.mark.asyncio
    async def test_cleanup_before_start(self, adapter: KoreAIAdapter) -> None:
        """Cleanup before adapter started doesn't raise."""
        await adapter.on_cleanup("room-abc")


# ---------------------------------------------------------------------------
# Template extractor
# ---------------------------------------------------------------------------


class TestTemplateExtractor:
    def test_string_passthrough(self) -> None:
        assert extract_text("Hello world") == "Hello world"

    def test_button_template(self) -> None:
        template = {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": "What do you want to do?",
                "buttons": [
                    {"type": "postback", "title": "Check Balance"},
                    {"type": "postback", "title": "Transfer Money"},
                    {"type": "web_url", "title": "Visit Website"},
                ],
            },
        }
        result = extract_text(template)
        assert "What do you want to do?" in result
        assert "- Check Balance" in result
        assert "- Transfer Money" in result
        assert "- Visit Website" in result

    def test_quick_reply_template(self) -> None:
        template = {
            "type": "template",
            "payload": {
                "template_type": "quick_reply",
                "text": "Choose an option:",
                "quick_replies": [
                    {"title": "Yes"},
                    {"title": "No"},
                ],
            },
        }
        result = extract_text(template)
        assert "Choose an option:" in result
        assert "- Yes" in result
        assert "- No" in result

    def test_carousel_template(self) -> None:
        template = {
            "type": "template",
            "payload": {
                "template_type": "generic",
                "elements": [
                    {"title": "Product A", "subtitle": "Best seller"},
                    {"title": "Product B", "subtitle": "New arrival"},
                ],
            },
        }
        result = extract_text(template)
        assert "1. Product A" in result
        assert "Best seller" in result
        assert "2. Product B" in result

    def test_list_template(self) -> None:
        template = {
            "type": "template",
            "payload": {
                "template_type": "list",
                "title": "Recent Orders",
                "elements": [
                    {"title": "Order #123"},
                    {"title": "Order #456"},
                ],
            },
        }
        result = extract_text(template)
        assert "Recent Orders" in result
        assert "- Order #123" in result
        assert "- Order #456" in result

    def test_list_input(self) -> None:
        result = extract_text(["Hello", "World"])
        assert "Hello" in result
        assert "World" in result

    def test_unknown_template_falls_back_to_json(self) -> None:
        template = {"type": "template", "payload": {"template_type": "custom_widget"}}
        result = extract_text(template)
        assert "custom_widget" in result

    def test_text_template(self) -> None:
        template = {
            "type": "template",
            "payload": {"template_type": "text", "text": "Simple text message"},
        }
        result = extract_text(template)
        assert result == "Simple text message"

    def test_empty_string(self) -> None:
        assert extract_text("") == ""


# ---------------------------------------------------------------------------
# Callback server
# ---------------------------------------------------------------------------


class TestCallbackServer:
    def test_register_turn_sets_active_room(self) -> None:
        server = KoreAICallbackServer()
        collector = server.register_turn("room-abc")
        assert collector.room_id == "room-abc"
        assert server._active_room == "room-abc"

    def test_unregister_turn_clears_active_room(self) -> None:
        server = KoreAICallbackServer()
        server.register_turn("room-abc")
        server.unregister_turn("room-abc")
        assert "room-abc" not in server._collectors
        assert server._active_room is None

    def test_unregister_nonexistent(self) -> None:
        server = KoreAICallbackServer()
        server.unregister_turn("room-nonexistent")  # No error

    def test_dispatch_text_callback_to_active_room(self) -> None:
        server = KoreAICallbackServer()
        collector = server.register_turn("room-abc")
        server._dispatch_callback({"text": "Hello from bot", "from": "st-bot123"})
        assert "Hello from bot" in collector.data.messages

    def test_dispatch_ignores_callback_when_no_active_room(self) -> None:
        server = KoreAICallbackServer()
        # No registered turn -- callback should be discarded
        server._dispatch_callback({"text": "Hello from bot", "from": "st-bot123"})

    def test_dispatch_task_completion_to_active_room(self) -> None:
        server = KoreAICallbackServer()
        collector = server.register_turn("room-abc")
        server._dispatch_callback(
            {
                "endOfTask": True,
                "endReason": "Fulfilled",
                "completedTaskName": "Show Balance",
            }
        )
        assert collector.data.task_completed
        assert collector.data.end_reason == "Fulfilled"
        assert not collector.data.is_agent_transfer

    def test_dispatch_agent_transfer(self) -> None:
        server = KoreAICallbackServer()
        collector = server.register_turn("room-abc")
        server._dispatch_callback(
            {
                "endOfTask": True,
                "endReason": "Interrupted",
                "completedTaskName": "Customer Support",
            }
        )
        assert collector.data.task_completed
        assert collector.data.is_agent_transfer
        assert collector.data.task_name == "Customer Support"

    def test_callback_routes_only_to_active_room(self) -> None:
        """Callbacks only go to the active room, not all collectors."""
        server = KoreAICallbackServer()
        collector_a = server.register_turn("room-a")
        # Registering room-b makes it the active room
        collector_b = server.register_turn("room-b")
        server._dispatch_callback({"text": "Response", "from": "st-bot"})
        # Only room-b (active) should receive the callback
        assert collector_b.data.messages == ["Response"]
        assert collector_a.data.messages == []


# ---------------------------------------------------------------------------
# Turn collector
# ---------------------------------------------------------------------------


class TestTurnCollector:
    @pytest.mark.asyncio
    async def test_wait_resolves_on_task_completion(self) -> None:
        collector = _TurnCollector("room-abc")
        collector.add_message("First message")
        collector.add_task_completion(
            end_reason="Fulfilled",
            task_name="Test",
            is_agent_transfer=False,
        )
        data = await collector.wait_for_messages(timeout=1.0)
        assert data.messages == ["First message"]
        assert data.task_completed

    @pytest.mark.asyncio
    async def test_wait_times_out(self) -> None:
        collector = _TurnCollector("room-abc")
        data = await collector.wait_for_messages(timeout=0.01)
        assert data.messages == []
        assert not data.task_completed

    @pytest.mark.asyncio
    async def test_multiple_messages_collected(self) -> None:
        collector = _TurnCollector("room-abc")
        collector.add_message("Part 1")
        collector.add_message("Part 2")
        collector.finish()
        data = await collector.wait_for_messages(timeout=1.0)
        assert data.messages == ["Part 1", "Part 2"]


# ---------------------------------------------------------------------------
# JWT auth
# ---------------------------------------------------------------------------


class TestJWTAuth:
    def test_generate_jwt(self) -> None:
        from thenvoi.integrations.koreai.auth import generate_jwt

        token = generate_jwt(
            client_id="cs-test",
            client_secret="my-secret-key-thats-long-enough-for-hmac",
            user_identity="room-abc",
        )
        assert isinstance(token, str)
        assert len(token) > 0

    def test_jwt_contains_correct_claims(self) -> None:
        import jwt as pyjwt

        from thenvoi.integrations.koreai.auth import generate_jwt

        secret = "my-secret-key-thats-long-enough-for-hmac"
        token = generate_jwt(
            client_id="cs-test",
            client_secret=secret,
            user_identity="room-abc",
            algorithm="HS256",
        )

        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["appId"] == "cs-test"
        assert decoded["userIdentity"] == "room-abc"
        assert "sub" in decoded
        assert "iat" in decoded
        assert "exp" in decoded

    def test_jwt_sub_is_numeric_string(self) -> None:
        import jwt as pyjwt

        from thenvoi.integrations.koreai.auth import generate_jwt

        secret = "my-secret-key-thats-long-enough-for-hmac"
        token = generate_jwt(
            client_id="cs-test",
            client_secret=secret,
            user_identity="room-abc",
        )

        decoded = pyjwt.decode(token, secret, algorithms=["HS256"])
        assert decoded["sub"].isdigit()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class TestHTTPClient:
    def test_webhook_url_construction(self) -> None:
        client = KoreAIClient(_VALID_CONFIG)
        assert (
            client._webhook_url == "https://bots.kore.ai/chatbot/v2/webhook/st-test-bot"
        )

    def test_webhook_url_strips_trailing_slash(self) -> None:
        cfg = KoreAIConfig(
            bot_id="st-test",
            client_id="cs-test",
            client_secret="secret",
            callback_url="https://example.com/callback",
            api_host="https://bots.kore.ai/",
        )
        client = KoreAIClient(cfg)
        assert client._webhook_url == "https://bots.kore.ai/chatbot/v2/webhook/st-test"
