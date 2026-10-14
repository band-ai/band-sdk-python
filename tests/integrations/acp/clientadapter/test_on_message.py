"""Tests for ACPClientAdapter.on_message()."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError
from acp.helpers import update_agent_message_text
from acp.schema import (
    NewSessionResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

from band.core.protocols import FAILURE_CODE_TIMEOUT, GENERIC_PROVIDER_FAILURE_MESSAGE
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
)
from band.integrations.acp.client_types import ACPClientSessionState
from band.testing import FakeAgentTools, events_of_type, reported_failures
from tests.integrations.acp.acp_toolkit.harness import (
    FakeSpawn,
    released,
    room_holder,
)
from tests.integrations.acp.clientadapter.kit import (
    CODEX,
    MOCK_ROOM,
    advertise_session_load,
    room_runtime,
)
from tests.integrations.acp.conftest import make_platform_message


class TestACPClientAdapterOnMessage:
    """Tests for ACPClientAdapter.on_message()."""

    @pytest.mark.asyncio
    async def test_on_message_creates_session(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should create ACP session for new room."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        mock_spawn.conn.new_session.assert_called_once()
        assert (
            adapter_with_mocks._rooms["room-123"].session.session_id
            == "acp-session-123"
        )

    @pytest.mark.asyncio
    async def test_on_message_applies_selected_session_configuration(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        effort = SessionConfigOptionSelect(
            id="reasoning_effort",
            name="Reasoning effort",
            type="select",
            current_value="medium",
            options=[
                SessionConfigSelectOption(value="medium", name="Medium"),
                SessionConfigSelectOption(value="high", name="High"),
            ],
        )
        mock_spawn.conn.new_session = AsyncMock(
            return_value=NewSessionResponse(
                session_id="acp-session-123", config_options=[effort]
            )
        )
        mock_spawn.conn.set_config_option = AsyncMock(
            return_value=SetSessionConfigOptionResponse(
                config_options=[effort.model_copy(update={"current_value": "high"})]
            )
        )
        resolver = AsyncMock(return_value={"reasoning_effort": "high"})
        adapter_with_mocks._resolve_session_config = resolver
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        resolver.assert_awaited_once()
        request = resolver.await_args.args[0]
        assert request.config_options == (effort,)
        mock_spawn.conn.set_config_option.assert_awaited_once_with(
            session_id="acp-session-123",
            config_id="reasoning_effort",
            value="high",
        )

    @pytest.mark.asyncio
    async def test_on_message_reuses_session(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should reuse existing session for same room."""
        for content in ("Hello", "Again"):
            await adapter_with_mocks.on_message(
                make_platform_message(content, room_id="room-123"),
                FakeAgentTools(),
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        mock_spawn.conn.new_session.assert_awaited_once()
        assert len(mock_spawn.conn.prompt.await_args_list) == 2

    @pytest.mark.asyncio
    async def test_on_message_sends_prompt(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should send prompt to remote ACP agent."""
        tools = FakeAgentTools()
        msg = make_platform_message("What is the weather?", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        mock_spawn.conn.prompt.assert_called_once()
        call_kwargs = mock_spawn.conn.prompt.call_args.kwargs
        assert call_kwargs["session_id"] == "acp-session-123"

    @pytest.mark.asyncio
    async def test_on_message_emits_task_event(
        self, adapter_with_mocks: ACPClientAdapter
    ) -> None:
        """Should emit task event for session rehydration."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Should have sent task event
        task_events = events_of_type(tools, "task")
        assert len(task_events) == 1
        assert task_events[0]["metadata"]["acp_client_session_id"] == "acp-session-123"

    @pytest.mark.asyncio
    async def test_on_message_bootstrap_rehydrates(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should rehydrate room -> session mappings on bootstrap."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        advertise_session_load(mock_spawn)
        mock_spawn.conn.load_session = AsyncMock(return_value=object())
        history = ACPClientSessionState(room_to_session={"room-123": "session-abc"})

        await adapter_with_mocks.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter_with_mocks._rooms["room-123"].session.session_id == "session-abc"
        mock_spawn.conn.load_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_message_creates_new_session_when_persisted_session_cannot_load(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A rebooted ephemeral ACP agent creates a session before prompting."""
        stale_session = "stale-session"
        fresh_session = MagicMock(session_id="fresh-session")
        mock_spawn.conn.new_session = AsyncMock(return_value=fresh_session)
        advertise_session_load(mock_spawn)
        mock_spawn.conn.load_session = AsyncMock(return_value=None)

        async def prompt_new_session(**kwargs):
            session_id = kwargs["session_id"]
            # Stream the reply through the live sink the adapter registers for the
            # turn (as a real agent would), not a direct buffer poke.
            await room_runtime(adapter_with_mocks)._client.session_update(
                session_id, update_agent_message_text("Recovered reply")
            )

        mock_spawn.conn.prompt = AsyncMock(side_effect=prompt_new_session)
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        await adapter_with_mocks.on_message(
            msg,
            tools,
            ACPClientSessionState(room_to_session={"room-123": stale_session}),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert (
            adapter_with_mocks._rooms["room-123"].session.session_id == "fresh-session"
        )
        mock_spawn.conn.new_session.assert_awaited_once()
        mock_spawn.conn.load_session.assert_awaited_once()
        prompt_calls = mock_spawn.conn.prompt.call_args_list
        assert [call.kwargs["session_id"] for call in prompt_calls] == ["fresh-session"]
        assert "[System Context]" in prompt_calls[0].kwargs["prompt"][0].text
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_on_message_error_sends_error_event(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """Should report an AgentFailure when the ACP agent fails."""
        mock_spawn.conn.prompt = AsyncMock(side_effect=RuntimeError("Agent crashed"))

        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        with pytest.raises(RuntimeError, match="Agent crashed"):
            await adapter_with_mocks.on_message(
                msg,
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "acp"
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.asyncio
    async def test_prompt_timeout_error_is_not_reported_as_adapter_timeout(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A provider-raised TimeoutError is not the adapter's deadline."""
        mock_spawn.conn.prompt = AsyncMock(
            side_effect=TimeoutError("provider socket timeout")
        )

        tools = FakeAgentTools()

        with pytest.raises(TimeoutError, match="provider socket timeout"):
            await adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["message"] == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert failures[0]["code"] is None

    @pytest.mark.asyncio
    async def test_adapter_deadline_raises_already_reported_failure(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """The adapter's own deadline reports once and remains retryable."""
        adapter_with_mocks.config = adapter_with_mocks.config.model_copy(
            update={"turn_timeout_s": 0.01}
        )

        async def slow_prompt(**_: object) -> None:
            await asyncio.sleep(1)

        mock_spawn.conn.prompt = AsyncMock(side_effect=slow_prompt)

        tools = FakeAgentTools()

        with pytest.raises(TimeoutError):
            await adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["code"] == FAILURE_CODE_TIMEOUT

    @pytest.mark.asyncio
    async def test_cancelling_the_turn_cancels_its_prompt(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A cancelled turn stops its prompt on both sides of the ACP connection."""
        prompt_started = asyncio.Event()
        prompt_cancelled = asyncio.Event()

        async def endless_prompt(**_: object) -> None:
            prompt_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                prompt_cancelled.set()
                raise

        conn = mock_spawn.conn
        conn.prompt = AsyncMock(side_effect=endless_prompt)
        turn = asyncio.create_task(
            adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                FakeAgentTools(),
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
        )
        await prompt_started.wait()

        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        assert prompt_cancelled.is_set()
        conn.cancel.assert_awaited_once_with("acp-session-123")

    @pytest.mark.asyncio
    async def test_cancelling_the_turn_survives_repeated_cancel(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A second STOP during session/cancel still finishes that cancel."""
        prompt_started = asyncio.Event()
        cancel_started = asyncio.Event()
        release_cancel = asyncio.Event()

        async def endless_prompt(**_: object) -> None:
            prompt_started.set()
            await asyncio.Event().wait()

        async def slow_cancel(session_id: str) -> None:
            cancel_started.set()
            await release_cancel.wait()

        conn = mock_spawn.conn
        conn.prompt = AsyncMock(side_effect=endless_prompt)
        conn.cancel = AsyncMock(side_effect=slow_cancel)
        turn = asyncio.create_task(
            adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                FakeAgentTools(),
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
        )
        await prompt_started.wait()
        turn.cancel()
        await cancel_started.wait()
        turn.cancel()
        await asyncio.sleep(0)
        assert not turn.done()
        release_cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await turn

        conn.cancel.assert_awaited_once_with("acp-session-123")

    @pytest.mark.asyncio
    async def test_timeout_cleanup_survives_outer_cancel(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """STOP during timeout cleanup still finishes session/cancel and failure."""
        adapter_with_mocks.config = adapter_with_mocks.config.model_copy(
            update={"turn_timeout_s": 0.05}
        )
        cancel_started = asyncio.Event()
        release_cancel = asyncio.Event()

        async def slow_prompt(**_: object) -> None:
            await asyncio.Event().wait()

        async def slow_cancel(session_id: str) -> None:
            cancel_started.set()
            await release_cancel.wait()

        conn = mock_spawn.conn
        conn.prompt = AsyncMock(side_effect=slow_prompt)
        conn.cancel = AsyncMock(side_effect=slow_cancel)
        tools = FakeAgentTools()
        turn = asyncio.create_task(
            adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
        )
        await cancel_started.wait()
        turn.cancel()
        await asyncio.sleep(0)
        # Bare shield would finish the outer task here while cleanup orphans.
        assert not turn.done()
        release_cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await turn

        conn.cancel.assert_awaited_once_with("acp-session-123")
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["code"] == FAILURE_CODE_TIMEOUT
        await released(adapter_with_mocks._rooms[MOCK_ROOM])
        assert adapter_with_mocks._rooms[MOCK_ROOM].runtime is None

    @pytest.mark.asyncio
    async def test_timeout_cleanup_survives_repeated_cancel(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A second STOP during drain still finishes session/cancel and failure."""
        adapter_with_mocks.config = adapter_with_mocks.config.model_copy(
            update={"turn_timeout_s": 0.05}
        )
        cancel_started = asyncio.Event()
        release_cancel = asyncio.Event()

        async def slow_prompt(**_: object) -> None:
            await asyncio.Event().wait()

        async def slow_cancel(session_id: str) -> None:
            cancel_started.set()
            await release_cancel.wait()

        conn = mock_spawn.conn
        conn.prompt = AsyncMock(side_effect=slow_prompt)
        conn.cancel = AsyncMock(side_effect=slow_cancel)
        tools = FakeAgentTools()
        turn = asyncio.create_task(
            adapter_with_mocks.on_message(
                make_platform_message("Hello", room_id="room-123"),
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )
        )
        await cancel_started.wait()
        turn.cancel()
        await asyncio.sleep(0)
        turn.cancel()
        await asyncio.sleep(0)
        assert not turn.done()
        release_cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await turn

        conn.cancel.assert_awaited_once_with("acp-session-123")
        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["code"] == FAILURE_CODE_TIMEOUT
        await released(adapter_with_mocks._rooms[MOCK_ROOM])
        assert adapter_with_mocks._rooms[MOCK_ROOM].runtime is None

    @pytest.mark.asyncio
    async def test_on_message_request_error_captures_code_and_data(
        self, adapter_with_mocks: ACPClientAdapter, mock_spawn: FakeSpawn
    ) -> None:
        """A JSON-RPC RequestError's code/data survive into the AgentFailure."""
        mock_spawn.conn.prompt = AsyncMock(
            side_effect=RequestError(-32603, "Internal error", {"detail": "oom"})
        )

        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        with pytest.raises(RequestError):
            await adapter_with_mocks.on_message(
                msg,
                tools,
                ACPClientSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        failures = reported_failures(tools)
        assert len(failures) == 1
        assert failures[0]["provider"] == "acp"
        assert failures[0]["code"] == "-32603"
        assert failures[0]["detail"] == {"detail": "oom"}

    @pytest.mark.asyncio
    async def test_an_unstarted_runtime_rejects_calls_without_spawning(self) -> None:
        """Only the room's acquisition starts a process, never a session call."""
        adapter = ACPClientAdapter(CODEX)
        runtime = adapter._build_runtime(room_holder(adapter, "room-123"), 1)

        with pytest.raises(RuntimeError, match="connection is not open"):
            await runtime.ensure_connection()
        assert runtime.closed
