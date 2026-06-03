"""Tests for ParlantAdapter with official Parlant SDK.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains Parlant-specific behavior: server/agent initialization,
Application container, session management, history injection, and error handling.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys

import pytest
from pydantic import BaseModel

try:
    import parlant.sdk  # type: ignore[missing-import]  # noqa: F401

    _HAS_PARLANT = True
except ImportError:
    _HAS_PARLANT = False

from band.adapters.parlant import ParlantAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage

# Parlant lives in the isolated `dev-parlant` dependency fork (it conflicts with
# crewai). The plain `test` CI job installs `--extra dev` without parlant and
# runs the whole suite, so these parlant-specific tests must skip cleanly there;
# they run for real in the `test-parlant` job.
pytestmark = pytest.mark.skipif(
    not _HAS_PARLANT,
    reason="parlant not installed (uv sync --extra dev-parlant)",
)


class CalculatorInput(BaseModel):
    """Calculate a value."""

    value: int


def calculate(args: CalculatorInput) -> str:
    return str(args.value + 1)


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.get_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call = AsyncMock(return_value={"status": "success"})
    return tools


@pytest.fixture
def mock_parlant_server():
    """Create mock Parlant SDK Server."""
    server = MagicMock()

    # Mock container with Application
    mock_app = MagicMock()
    mock_app.sessions = AsyncMock()
    mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
    mock_app.sessions.create_customer_message = AsyncMock(
        return_value=MagicMock(offset=1)
    )
    mock_app.sessions.create_event = AsyncMock()
    mock_app.sessions.wait_for_update = AsyncMock(return_value=True)
    mock_app.sessions.find_events = AsyncMock(return_value=[])

    # Container returns Application
    server.container = {MagicMock: mock_app}

    # Mock customer lookup/creation
    server.find_customer = AsyncMock(return_value=None)
    server.create_customer = AsyncMock(return_value=MagicMock(id="customer-123"))

    return server


@pytest.fixture
def mock_parlant_agent():
    """Create mock Parlant Agent."""
    agent = MagicMock()
    agent.id = "parlant-agent-123"
    agent.name = "TestBot"
    agent.create_guideline = AsyncMock(return_value=MagicMock(id="guideline-123"))
    return agent


class TestInitialization:
    """Tests for adapter initialization."""

    def test_initialization_with_server_and_agent(
        self, mock_parlant_server, mock_parlant_agent
    ):
        """Should initialize with server and agent."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )

        assert adapter._server is mock_parlant_server
        assert adapter._parlant_agent is mock_parlant_agent

    def test_internal_state_initialized(self, mock_parlant_server, mock_parlant_agent):
        """Should initialize internal state correctly."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )

        assert adapter._app is None
        assert adapter._room_sessions == {}
        assert adapter._room_customers == {}
        assert adapter._system_prompt == ""

    def test_stores_additional_tools_for_contract_guideline(
        self, mock_parlant_server, mock_parlant_agent
    ):
        """Parlant should expose CustomToolDef tools through its guideline tools."""
        custom_tool = (CalculatorInput, calculate)

        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            additional_tools=[custom_tool],
        )

        assert adapter._custom_tools == [custom_tool]


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.fixture
    def mock_application_class(self):
        """Create a mock Application class for testing."""
        return MagicMock(name="Application")

    @pytest.mark.asyncio
    async def test_renders_system_prompt(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Should render system prompt from agent metadata."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )

        mock_app = MagicMock()

        # Create a mock module with Application
        mock_module = MagicMock()
        mock_module.Application = mock_application_class

        # Set up container to return app when accessed with Application class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._system_prompt != ""
        assert "TestBot" in adapter._system_prompt

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt_if_provided(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Should use custom system_prompt if provided."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            system_prompt="You are a custom assistant.",
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._system_prompt == "You are a custom assistant."

    @pytest.mark.asyncio
    async def test_installs_rendered_prompt_as_parlant_guideline(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Should install Band platform instructions into Parlant, not just store them."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            custom_section="Always include the token BANANA.",
            features=AdapterFeatures(capabilities={Capability.CONTACTS}),
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started(
                agent_name="BandBot", agent_description="A Band test agent"
            )

        mock_parlant_agent.create_guideline.assert_awaited_once()
        kwargs = mock_parlant_agent.create_guideline.await_args.kwargs
        assert "BANANA" in kwargs["description"]
        assert "BandBot" in kwargs["description"]
        assert kwargs["metadata"]["band_adapter_contract"] is True
        assert kwargs["matcher"] is not None
        assert any(t.tool.name == "band_send_message" for t in kwargs["tools"])
        assert any(t.tool.name == "band_list_contacts" for t in kwargs["tools"])
        assert adapter._contract_guideline_installed is True
        assert adapter._contract_guideline_id == "guideline-123"

    @pytest.mark.asyncio
    async def test_contract_guideline_includes_additional_tools(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """CustomToolDef tools should be exposed through Parlant's tool surface."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            additional_tools=[(CalculatorInput, calculate)],
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started("BandBot", "A Band test agent")

        tools = mock_parlant_agent.create_guideline.await_args.kwargs["tools"]
        calculator = next(t for t in tools if t.tool.name == "calculator")
        assert list(calculator.tool.parameters) == ["value"]

    @pytest.mark.asyncio
    async def test_contract_guideline_requires_explicit_contacts_capability(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Omitted features should not expose contact-management tools."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started("BandBot", "A Band test agent")

        tools = mock_parlant_agent.create_guideline.await_args.kwargs["tools"]
        assert not any(t.tool.name == "band_list_contacts" for t in tools)

    @pytest.mark.asyncio
    async def test_contract_guideline_respects_explicit_empty_features(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Explicit empty features should not expose contact tools."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            features=AdapterFeatures(),
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started("BandBot", "A Band test agent")

        tools = mock_parlant_agent.create_guideline.await_args.kwargs["tools"]
        assert not any(t.tool.name == "band_list_contacts" for t in tools)

    @pytest.mark.asyncio
    async def test_gets_application_from_container(
        self, mock_parlant_server, mock_parlant_agent, mock_application_class
    ):
        """Should get Application from Parlant container."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )

        mock_app = MagicMock()
        mock_module = MagicMock()
        mock_module.Application = mock_application_class
        mock_parlant_server.container = {mock_application_class: mock_app}

        with patch.dict(
            sys.modules,
            {"parlant.core.application": mock_module},
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

        assert adapter._app is mock_app


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.fixture
    def initialized_adapter(self, mock_parlant_server, mock_parlant_agent):
        """Create an initialized adapter with mocked app."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter.agent_name = "TestBot"
        adapter.agent_description = "A test bot"
        adapter._system_prompt = "Test prompt"

        # Mock the application
        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        mock_app.sessions.find_events = AsyncMock(
            return_value=[
                MagicMock(
                    offset=2,
                    kind="message",
                    source="ai_agent",
                    data={"message": "Hello from Parlant"},
                )
            ]
        )

        adapter._app = mock_app
        return adapter

    @pytest.mark.asyncio
    async def test_creates_session_for_room(
        self, initialized_adapter, sample_message, mock_tools, mock_parlant_server
    ):
        """Should create or get session for room."""
        # Mock imports
        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=MagicMock(NONE="none")
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=MagicMock(CUSTOMER="customer", AI_AGENT="ai_agent"),
                    EventKind=MagicMock(MESSAGE="message"),
                ),
                "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
            },
        ):
            await initialized_adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # Verify session was created
        assert "room-123" in initialized_adapter._room_sessions
        mock_parlant_server.create_customer.assert_called_once()

    @pytest.mark.asyncio
    async def test_customer_ids_do_not_truncate_room_id_prefixes(
        self, initialized_adapter, mock_parlant_server
    ):
        """Rooms with the same first eight characters should not share a customer id."""
        await initialized_adapter._get_or_create_customer("abcdefgh-room-one", "Alice")
        await initialized_adapter._get_or_create_customer("abcdefgh-room-two", "Bob")

        first_call, second_call = mock_parlant_server.create_customer.await_args_list
        assert first_call.kwargs["id"] != second_call.kwargs["id"]

    @pytest.mark.asyncio
    async def test_get_or_create_customer_reuses_existing_parlant_customer(
        self, initialized_adapter, mock_parlant_server
    ):
        """Adapter restarts against the same Parlant server should be idempotent."""
        mock_parlant_server.find_customer.return_value = MagicMock(
            id="existing-customer"
        )

        customer_id = await initialized_adapter._get_or_create_customer(
            "room-123", "Alice"
        )

        assert customer_id == "existing-customer"
        mock_parlant_server.create_customer.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_customer_message_to_parlant(
        self, initialized_adapter, sample_message, mock_tools
    ):
        """Should send customer message to Parlant."""
        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"

        mock_event_source = MagicMock()
        mock_event_source.CUSTOMER = "customer"
        mock_event_source.AI_AGENT = "ai_agent"

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=mock_event_source,
                    EventKind=MagicMock(MESSAGE="message"),
                ),
                "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
            },
        ):
            await initialized_adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        # Verify message was sent to Parlant
        initialized_adapter._app.sessions.create_customer_message.assert_called_once()
        wait_kwargs = (
            initialized_adapter._app.sessions.wait_for_more_events.await_args.kwargs
        )
        find_kwargs = initialized_adapter._app.sessions.find_events.await_args.kwargs
        assert wait_kwargs["source"] == "ai_agent"
        assert find_kwargs["source"] == "ai_agent"

    @pytest.mark.asyncio
    async def test_sets_session_tools_for_tool_execution(
        self, initialized_adapter, sample_message, mock_tools
    ):
        """Should set session tools for Parlant tool execution."""
        with patch("band.adapters.parlant.set_session_tools") as mock_set_tools:
            mock_moderation = MagicMock()
            mock_moderation.NONE = "none"

            with patch.dict(
                sys.modules,
                {
                    "parlant.core.app_modules.sessions": MagicMock(
                        Moderation=mock_moderation
                    ),
                    "parlant.core.sessions": MagicMock(
                        EventSource=MagicMock(CUSTOMER="customer", AI_AGENT="ai_agent"),
                        EventKind=MagicMock(MESSAGE="message"),
                    ),
                    "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
                },
            ):
                await initialized_adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Verify tools were set with session_id and then cleared
            assert mock_set_tools.call_count == 2
            # First call sets the tools with session_id + emit flag (off by default)
            mock_set_tools.assert_any_call(
                "session-123", mock_tools, emit_execution=False
            )
            # Second call clears the tools
            mock_set_tools.assert_any_call("session-123", None)

    @pytest.mark.asyncio
    async def test_response_loop_only_waits_for_agent_message(
        self, mock_parlant_server, mock_parlant_agent, sample_message, mock_tools
    ):
        """Execution reporting moved into the tool wrappers, so the response loop
        only waits for the agent's final MESSAGE — it no longer polls TOOL events
        or widens the source filter, which is what produced misordered/duplicate
        tool events."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
        adapter.agent_name = "TestBot"
        adapter.agent_description = "A test bot"
        adapter._system_prompt = "Test prompt"

        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        mock_app.sessions.find_events = AsyncMock(
            return_value=[
                MagicMock(
                    id="evt-message",
                    offset=3,
                    kind="message",
                    source="ai_agent",
                    data={"message": "Done"},
                ),
            ]
        )
        adapter._app = mock_app

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"
        mock_event_kind = MagicMock(MESSAGE="message", TOOL="tool")
        mock_event_source = MagicMock(
            CUSTOMER="customer",
            AI_AGENT="ai_agent",
            SYSTEM="system",
        )

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=mock_event_source,
                    EventKind=mock_event_kind,
                ),
                "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
            },
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        wait_kwargs = mock_app.sessions.wait_for_more_events.await_args.kwargs
        find_kwargs = mock_app.sessions.find_events.await_args.kwargs
        # Only the final agent message is awaited; TOOL polling is gone.
        assert wait_kwargs["kinds"] == ["message"]
        assert find_kwargs["kinds"] == ["message"]
        assert wait_kwargs["source"] == "ai_agent"
        assert find_kwargs["source"] == "ai_agent"
        # The adapter no longer re-reports tool events from the session log.
        mock_tools.send_event.assert_not_called()
        mock_tools.send_message.assert_awaited_once_with("Done", mentions=["Alice"])

    @pytest.mark.asyncio
    async def test_emit_flag_passed_to_session_tools(
        self, mock_parlant_server, mock_parlant_agent, sample_message, mock_tools
    ):
        """The adapter must tell the wrappers whether execution emit is enabled."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            features=AdapterFeatures(emit={Emit.EXECUTION}),
        )
        adapter.agent_name = "TestBot"
        adapter.agent_description = "A test bot"
        adapter._system_prompt = "Test prompt"

        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        mock_app.sessions.find_events = AsyncMock(
            return_value=[
                MagicMock(
                    id="evt-message",
                    offset=3,
                    kind="message",
                    source="ai_agent",
                    data={"message": "Done"},
                ),
            ]
        )
        adapter._app = mock_app

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"
        mock_event_kind = MagicMock(MESSAGE="message", TOOL="tool")
        mock_event_source = MagicMock(CUSTOMER="customer", AI_AGENT="ai_agent")

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=mock_event_source,
                    EventKind=mock_event_kind,
                ),
                "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
            },
        ):
            with patch("band.adapters.parlant.set_session_tools") as mock_set_tools:
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        mock_set_tools.assert_any_call("session-123", mock_tools, emit_execution=True)

    @pytest.mark.asyncio
    async def test_reuses_existing_session(
        self, initialized_adapter, sample_message, mock_tools, mock_parlant_server
    ):
        """Should reuse existing session for same room."""
        # Pre-populate session
        initialized_adapter._room_sessions["room-123"] = "existing-session"
        initialized_adapter._room_customers["room-123"] = "existing-customer"

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=MagicMock(CUSTOMER="customer", AI_AGENT="ai_agent"),
                    EventKind=MagicMock(MESSAGE="message"),
                ),
                "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
            },
        ):
            await initialized_adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Should not create new customer/session
        mock_parlant_server.create_customer.assert_not_called()
        initialized_adapter._app.sessions.create.assert_not_called()


class TestOnCleanup:
    """Tests for on_cleanup() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_session(self, mock_parlant_server, mock_parlant_agent):
        """Should clean up Parlant session."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter._room_sessions["room-123"] = "session-123"
        adapter._room_customers["room-123"] = "customer-123"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_sessions
        assert "room-123" not in adapter._room_customers


class TestHistoryInjection:
    """Tests for history injection."""

    @pytest.fixture
    def adapter_with_app(self, mock_parlant_server, mock_parlant_agent):
        """Create adapter with mocked application."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter.agent_name = "TestBot"

        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        mock_app.sessions.create_event = AsyncMock()

        adapter._app = mock_app
        return adapter

    @pytest.mark.asyncio
    async def test_injects_complete_exchanges_only(self, adapter_with_app):
        """Should only inject complete user-assistant exchanges."""
        history = [
            {"role": "user", "content": "Hello", "sender": "Alice"},
            {"role": "assistant", "content": "Hi there!", "sender": "TestBot"},
            {
                "role": "user",
                "content": "Pending question",
            },  # No response - should skip
        ]

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"

        mock_event_kind = MagicMock()
        mock_event_kind.MESSAGE = "message"

        mock_event_source = MagicMock()
        mock_event_source.CUSTOMER = "customer"
        mock_event_source.AI_AGENT = "ai_agent"

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventKind=mock_event_kind,
                    EventSource=mock_event_source,
                ),
            },
        ):
            count = await adapter_with_app._inject_history("session-123", history)

        # Should inject 2 messages (complete exchange), skip the pending question
        assert count == 2

    @pytest.mark.asyncio
    async def test_handles_empty_history(self, adapter_with_app):
        """Should handle empty history gracefully."""
        count = await adapter_with_app._inject_history("session-123", [])
        assert count == 0


class TestCleanupAll:
    """Tests for cleanup_all() method."""

    @pytest.mark.asyncio
    async def test_cleans_up_all_sessions(
        self, mock_parlant_server, mock_parlant_agent
    ):
        """Should cleanup all sessions."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter._room_sessions["room-1"] = "session-1"
        adapter._room_sessions["room-2"] = "session-2"
        adapter._room_customers["room-1"] = "customer-1"
        adapter._room_customers["room-2"] = "customer-2"

        await adapter.cleanup_all()

        assert len(adapter._room_sessions) == 0
        assert len(adapter._room_customers) == 0


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_reports_error_on_failure(
        self, mock_parlant_server, mock_parlant_agent, sample_message, mock_tools
    ):
        """Should report error when processing fails."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter.agent_name = "TestBot"
        adapter._system_prompt = "Test prompt"

        # Mock app that fails on create_customer_message
        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            side_effect=Exception("API error")
        )
        adapter._app = mock_app

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"

        with patch.dict(
            sys.modules,
            {
                "parlant.core.app_modules.sessions": MagicMock(
                    Moderation=mock_moderation
                ),
                "parlant.core.sessions": MagicMock(
                    EventSource=MagicMock(CUSTOMER="customer"),
                ),
            },
        ):
            with pytest.raises(Exception, match="API error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        # Should have tried to report error
        mock_tools.send_event.assert_called()

    @pytest.mark.asyncio
    async def test_clears_tools_on_error(
        self, mock_parlant_server, mock_parlant_agent, sample_message, mock_tools
    ):
        """Should clear tools even when error occurs."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        adapter.agent_name = "TestBot"
        adapter._system_prompt = "Test prompt"

        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            side_effect=Exception("API error")
        )
        adapter._app = mock_app

        mock_moderation = MagicMock()
        mock_moderation.NONE = "none"

        with patch("band.adapters.parlant.set_session_tools") as mock_set_tools:
            with patch.dict(
                sys.modules,
                {
                    "parlant.core.app_modules.sessions": MagicMock(
                        Moderation=mock_moderation
                    ),
                    "parlant.core.sessions": MagicMock(
                        EventSource=MagicMock(CUSTOMER="customer"),
                    ),
                },
            ):
                with pytest.raises(Exception):
                    await adapter.on_message(
                        msg=sample_message,
                        tools=mock_tools,
                        history=[],
                        participants_msg=None,
                        contacts_msg=None,
                        is_session_bootstrap=True,
                        room_id="room-123",
                    )

            # Tools should be cleared in finally block with session_id
            mock_set_tools.assert_any_call("session-123", None)

    @pytest.mark.asyncio
    async def test_handles_uninitialized_app(
        self, mock_parlant_server, mock_parlant_agent, sample_message, mock_tools
    ):
        """Should handle case when app is not initialized."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        # Don't set _app

        with pytest.raises(RuntimeError, match="Parlant Application not initialized"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_message.assert_not_called()
        mock_tools.send_event.assert_called_once()
