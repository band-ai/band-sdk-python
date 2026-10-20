from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    RoomContext,
)
from band.converters.letta import LettaSessionState
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.core.types import Capability, Emit
from band.testing import (
    FakeAgentTools,
    reported_failures,
)
from tests.adapters.letta.kit import REPLY_SETTLING_CALLS, sent_contents
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_mock_agent,
    make_mock_tool_page,
    make_platform_message,
    make_tool_call_message,
    make_tool_return_message,
    ready_letta_adapter,
    scripted_letta_turn,
)


class TestLettaAdapterOnMessagePerRoom:
    """Tests for per_room mode message handling."""

    @pytest.fixture
    def adapter_with_client(self) -> tuple[LettaAdapter, AsyncMock]:
        adapter = LettaAdapter()
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test prompt"
        adapter._mcp.tool_ids = ["t1", "t2"]
        adapter._mcp.server_id = "mcp-server-1"
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_basic_message_creates_agent_and_sends(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        mock_agent = make_mock_agent("agent-1")
        mock_client.agents.create.return_value = mock_agent
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Hello!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Agent was created
        mock_client.agents.create.assert_called_once()
        # MCP tools were attached
        assert mock_client.agents.tools.attach.call_count == 2
        # Message was sent via direct agent API (not conversations)
        mock_client.agents.messages.create.assert_called_once()
        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "agent-1"
        assert "conversation_id" not in call_kwargs

    @pytest.mark.asyncio
    async def test_configured_memory_blocks_follow_the_persona_block(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        human = {"label": "human", "value": "Prefers short answers."}
        adapter.config = LettaAdapterConfig(memory_blocks=(human,))
        mock_client.agents.create.return_value = make_mock_agent("agent-1")

        await adapter._create_agent("room-1")

        blocks = mock_client.agents.create.call_args.kwargs["memory_blocks"]
        assert [block["label"] for block in blocks] == ["persona", "human"]
        assert blocks[1] == human

    @pytest.mark.asyncio
    async def test_auto_relay_when_no_send_message(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        # Setup room with agent
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'll help you!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Auto-relay should have sent the message
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "I'll help you!"

    @pytest.mark.asyncio
    async def test_send_message_failure_is_not_reported_as_provider_failure(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """The Letta agent answered fine; the room POST is what failed. That
        must not surface as a Letta AgentFailure -- deliver_reply's
        DeliveryFailedError must be recognized and left unreported here."""
        adapter, mock_client = adapter_with_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'll help you!")
        )

        tools = FakeAgentTools()

        async def _raise(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("platform rejected the message")

        tools.send_message = _raise  # type: ignore[method-assign]

        msg = make_platform_message()
        history = LettaSessionState()

        with pytest.raises(RuntimeError, match="platform rejected the message"):
            await adapter.on_message(
                msg,
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        assert not reported_failures(tools)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("reply_tool", "arguments"), REPLY_SETTLING_CALLS)
    async def test_skip_auto_relay_when_a_reply_tool_settled_the_turn(
        self, reply_tool: str, arguments: dict[str, Any]
    ) -> None:
        adapter, mock_client = ready_letta_adapter(capabilities={Capability.FILES})
        mock_client.agents.messages.create.side_effect = scripted_letta_turn(
            adapter,
            room_id="room-1",
            tool_calls=[(reply_tool, arguments)],
            final_text="Done!",
        )
        tools = FakeAgentTools()

        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert "Done!" not in sent_contents(tools)

    @pytest.mark.asyncio
    async def test_timeout_reports_error(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client
        adapter.config = adapter.config.model_copy(update={"turn_timeout_s": 0.01})

        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        async def slow_response(**kwargs: Any) -> MagicMock:
            await asyncio.sleep(1)
            return make_letta_response()

        mock_client.agents.messages.create.side_effect = slow_response

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_message(
                msg,
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert "timed out" in failures[0]["message"]
        assert failures[0]["provider"] == "letta"
        assert failures[0]["code"] == "timeout"

    @pytest.mark.asyncio
    async def test_slow_delivery_is_not_misreported_as_provider_timeout(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """turn_timeout_s bounds only the Letta round-trip. A room POST that
        is merely slow (not the Letta call) must not be misreported as a
        Letta provider timeout -- it must be given time to complete."""
        adapter, mock_client = adapter_with_client
        adapter.config = adapter.config.model_copy(update={"turn_timeout_s": 0.05})

        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        # The Letta call itself responds instantly, well inside turn_timeout_s.
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("I'll help you!")
        )

        tools = FakeAgentTools()
        real_send_message = tools.send_message

        async def _slow_send_message(*args: Any, **kwargs: Any) -> Any:
            # Longer than turn_timeout_s: only the provider call may race it.
            await asyncio.sleep(0.15)
            return await real_send_message(*args, **kwargs)

        tools.send_message = _slow_send_message  # type: ignore[method-assign]

        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 1
        assert not reported_failures(tools)

    @pytest.mark.asyncio
    async def test_send_event_timeout_is_not_misreported_as_provider_timeout(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A TimeoutError from tool-event reporting (send_event) -- not the
        Letta round-trip itself -- must fall through to the generic failure
        path, never be mislabeled as a Letta provider timeout. (send_failure
        itself best-effort-swallows the same broken channel here, same as
        production, so the room never receives a failure event either way --
        what this guards is which branch is taken/logged.)"""
        config = LettaAdapterConfig()
        adapter = LettaAdapter(config=config, emit=Emit.TOOL_CALLS)
        mock_client = AsyncMock()
        adapter._client = mock_client
        adapter._system_prompt = "Test"
        adapter._mcp.tool_ids = []
        adapter._mcp.server_id = "mcp-server-1"
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        # The Letta round-trip itself returns instantly -- well inside
        # turn_timeout_s -- so any TimeoutError must come from elsewhere.
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("band_lookup_peers", "{}"),
            make_tool_return_message("band_lookup_peers", '{"peers": []}'),
        )

        tools = FakeAgentTools()
        tools.send_event_error = TimeoutError("event POST hiccup")
        msg = make_platform_message()
        history = LettaSessionState()

        with (
            caplog.at_level(logging.ERROR, logger="band.adapters.letta"),
            pytest.raises(TimeoutError),
        ):
            await adapter.on_message(
                msg,
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        assert not any("timed out" in r.message for r in caplog.records)
        assert any("Error during Letta turn" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_generic_exception_reports_and_propagates(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        """A bare exception from the Letta client (not a timeout, not a
        delivery failure) must still surface via the generic fallback."""
        adapter, mock_client = adapter_with_client
        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")
        mock_client.agents.messages.create.side_effect = ConnectionError(
            "letta connection reset"
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        with pytest.raises(ConnectionError, match="letta connection reset"):
            await adapter.on_message(
                msg,
                tools,
                history,
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        failure = reported_failures(tools)[0]
        assert failure["provider"] == "letta"
        assert failure["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert "letta connection reset" not in failure["message"]

    @pytest.mark.asyncio
    async def test_participants_and_contacts_injected(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        adapter._rooms["room-1"] = RoomContext(agent_id="agent-1")

        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Got it.")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        await adapter.on_message(
            msg,
            tools,
            history,
            "Participants: Alice, Bob",
            "Contacts: Charlie",
            is_session_bootstrap=False,
            room_id="room-1",
        )

        call_kwargs = mock_client.agents.messages.create.call_args.kwargs
        content = call_kwargs["messages"][0]["content"]
        assert "[System]: Participants: Alice, Bob" in content
        assert "[System]: Contacts: Charlie" in content

    @pytest.mark.asyncio
    async def test_agent_resume_from_history(
        self, adapter_with_client: tuple[LettaAdapter, AsyncMock]
    ) -> None:
        adapter, mock_client = adapter_with_client

        mock_client.agents.retrieve.return_value = make_mock_agent("history-agent")
        mock_client.agents.tools.list.return_value = make_mock_tool_page()
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_assistant_message("Resumed!")
        )

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState(agent_id="history-agent")

        await adapter.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Agent was retrieved, not created
        mock_client.agents.retrieve.assert_called_once_with("history-agent")
        mock_client.agents.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_uninitialized_client_reports_error(self) -> None:
        adapter = LettaAdapter()
        # _client is None

        tools = FakeAgentTools()
        msg = make_platform_message()
        history = LettaSessionState()

        with pytest.raises(RuntimeError, match="not initialized"):
            await adapter.on_message(
                msg,
                tools,
                history,
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "letta"
        assert "not initialized" in failures[0]["message"]
