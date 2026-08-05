"""Tests for ParlantAdapter with official Parlant SDK.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_started agent_name/description, on_message callable,
cleanup safety) live in tests/framework_conformance/test_adapter_conformance.py.
This file contains Parlant-specific behavior: server/agent initialization,
Application container, session management, history injection, and error handling.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys

import pytest

from band.adapters.parlant import ParlantAdapter
from band.core.types import PlatformMessage


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
    mock_app.sessions.wait_for_more_events = AsyncMock(return_value=False)
    mock_app.sessions.find_events = AsyncMock(return_value=[])

    # Container returns Application
    server.container = {MagicMock: mock_app}

    # Mock create_customer / create_agent
    server.create_customer = AsyncMock(return_value=MagicMock(id="customer-123"))
    created_agent = MagicMock()
    created_agent.id = "parlant-agent-created"
    created_agent.create_guideline = AsyncMock()
    server.create_agent = AsyncMock(return_value=created_agent)

    return server


@pytest.fixture
def mock_parlant_agent():
    """Create mock Parlant Agent."""
    agent = MagicMock()
    agent.id = "parlant-agent-123"
    agent.name = "TestBot"
    agent.create_guideline = AsyncMock()
    return agent


@pytest.fixture(autouse=True)
def stub_band_tools():
    """Stub the Band->Parlant tool build in on_started.

    The real ``create_parlant_tools`` imports ``parlant.sdk``; doing that mid-suite,
    after other tests have patched ``parlant.core.*`` into ``sys.modules``, corrupts
    beartype's import hook and breaks later genuine parlant imports.
    """
    with patch("band.adapters.parlant.create_parlant_tools", return_value=[]) as stub:
        yield stub


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

    def test_prompt_params_rejected_with_borrowed_agent(
        self, mock_parlant_server, mock_parlant_agent
    ):
        """system_prompt/custom_section only shape an adapter-created agent."""
        with pytest.raises(ValueError, match="parlant_agent"):
            ParlantAdapter(
                server=mock_parlant_server,
                parlant_agent=mock_parlant_agent,
                system_prompt="You are a custom assistant.",
            )
        with pytest.raises(ValueError, match="parlant_agent"):
            ParlantAdapter(
                server=mock_parlant_server,
                parlant_agent=mock_parlant_agent,
                custom_section="Be helpful.",
            )


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.fixture
    def mock_application_class(self):
        """Create a mock Application class for testing."""
        return MagicMock(name="Application")

    @pytest.mark.asyncio
    async def test_custom_section_appended_to_created_agent_description(
        self, mock_parlant_server, mock_application_class
    ):
        """custom_section must reach the created Parlant agent's description."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            custom_section="Be helpful.",
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

        mock_parlant_server.create_agent.assert_awaited_once_with(
            name="TestBot",
            description="A test bot\n\nBe helpful.",
        )

    @pytest.mark.asyncio
    async def test_system_prompt_overrides_created_agent_description(
        self, mock_parlant_server, mock_application_class
    ):
        """system_prompt must fully replace the created agent's description."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
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

        mock_parlant_server.create_agent.assert_awaited_once_with(
            name="TestBot",
            description="You are a custom assistant.",
        )

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


class TestLifecycleOwnedServer:
    """The adapter boots/owns the Parlant server inside its own lifecycle."""

    @pytest.fixture
    def application_modules(self):
        """Mock parlant.core.application so on_started's import resolves."""
        application_class = MagicMock(name="Application")
        module = MagicMock()
        module.Application = application_class
        with patch.dict(sys.modules, {"parlant.core.application": module}):
            yield application_class

    @pytest.fixture
    def owned_server(
        self, mock_parlant_server, mock_parlant_agent, application_modules
    ):
        """Patch running_parlant_server with a fake CM yielding the mock server."""
        mock_parlant_server.create_agent = AsyncMock(return_value=mock_parlant_agent)
        mock_parlant_server.container = {application_modules: MagicMock()}
        cm = MagicMock()

        async def enter():
            setup = factory.call_args.kwargs["setup"]
            await setup(mock_parlant_server)
            return mock_parlant_server

        cm.__aenter__ = AsyncMock(side_effect=enter)
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "band.adapters.parlant.running_parlant_server", return_value=cm
        ) as factory:
            yield factory, cm, mock_parlant_server

    async def test_boots_owned_server_and_creates_agent(
        self, owned_server, mock_parlant_agent
    ):
        factory, cm, server = owned_server
        adapter = ParlantAdapter(name="Tom", description="A cat", nlp_service="svc")

        await adapter.on_started("BandName", "Band description")

        assert factory.call_count == 1
        assert factory.call_args.kwargs["nlp_service"] == "svc"
        assert callable(factory.call_args.kwargs["setup"])
        cm.__aenter__.assert_awaited_once()
        server.create_agent.assert_awaited_once_with(name="Tom", description="A cat")
        assert adapter.server is server
        assert adapter.parlant_agent is mock_parlant_agent
        assert adapter._app is not None

    async def test_name_description_default_to_band_metadata(self, owned_server):
        _, _, server = owned_server
        adapter = ParlantAdapter()

        await adapter.on_started("BandName", "Band description")

        server.create_agent.assert_awaited_once_with(
            name="BandName", description="Band description"
        )

    async def test_applies_deferred_guidelines_with_band_tools_default(
        self, owned_server, mock_parlant_agent, stub_band_tools
    ):
        band_tools = ["band-tool-entry"]
        stub_band_tools.return_value = band_tools
        adapter = ParlantAdapter(name="X", description="Y")
        adapter.add_guideline(condition="c1", action="a1")
        adapter.add_guideline(condition="c2", action="a2", tools=[])
        adapter.add_guideline(condition="c3", action="a3", metadata={"k": "v"})

        await adapter.on_started("BandName", "Band description")

        calls = mock_parlant_agent.create_guideline.await_args_list
        assert [c.kwargs for c in calls] == [
            {"condition": "c1", "action": "a1", "tools": band_tools},
            {"condition": "c2", "action": "a2", "tools": []},
            {
                "condition": "c3",
                "action": "a3",
                "tools": band_tools,
                "metadata": {"k": "v"},
            },
        ]

    async def test_guideline_failure_has_no_live_siblings_and_retries_from_failure(
        self, mock_parlant_server, mock_parlant_agent, application_modules
    ):
        """A failed create checkpoints earlier work and never starts later work."""
        mock_parlant_server.container = {application_modules: MagicMock()}
        mock_parlant_agent.create_guideline = AsyncMock(
            side_effect=[None, RuntimeError("bad guideline"), None, None]
        )
        adapter = ParlantAdapter(
            server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )
        adapter.add_guideline(condition="first", action="done")
        adapter.add_guideline(condition="second", action="retry")
        adapter.add_guideline(condition="third", action="later")

        with pytest.raises(RuntimeError, match="bad guideline"):
            await adapter.on_started("BandName", "Band description")

        assert [
            call.kwargs["condition"]
            for call in mock_parlant_agent.create_guideline.await_args_list
        ] == ["first", "second"]

        await adapter.on_started("BandName", "Band description")

        assert [
            call.kwargs["condition"]
            for call in mock_parlant_agent.create_guideline.await_args_list
        ] == ["first", "second", "second", "third"]

    async def test_add_guideline_after_start_raises(self, owned_server):
        adapter = ParlantAdapter(name="X", description="Y")
        await adapter.on_started("BandName", "Band description")

        with pytest.raises(RuntimeError, match="before the agent starts"):
            adapter.add_guideline(condition="late", action="too late")

    async def test_configure_callback_receives_live_objects(
        self, owned_server, mock_parlant_agent
    ):
        _, _, server = owned_server
        seen: list[tuple] = []

        async def configure(srv, agent):
            seen.append((srv, agent))

        adapter = ParlantAdapter(name="X", description="Y", configure=configure)
        await adapter.on_started("BandName", "Band description")

        assert seen == [(server, mock_parlant_agent)]

    async def test_cleanup_all_closes_owned_server(self, owned_server):
        _, cm, _ = owned_server
        adapter = ParlantAdapter(name="X", description="Y")
        await adapter.on_started("BandName", "Band description")

        await adapter.cleanup_all()

        cm.__aexit__.assert_awaited_once()
        assert adapter._server is None
        assert adapter._app is None

    async def test_cleanup_all_leaves_borrowed_server(
        self, mock_parlant_server, mock_parlant_agent, application_modules
    ):
        mock_parlant_server.container = {application_modules: MagicMock()}
        adapter = ParlantAdapter(
            server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )
        await adapter.on_started("BandName", "Band description")

        await adapter.cleanup_all()

        assert adapter._server is mock_parlant_server
        assert adapter._parlant_agent is mock_parlant_agent
        assert adapter._app is None

    async def test_restart_with_borrowed_server_does_not_duplicate_guidelines(
        self, mock_parlant_server, mock_parlant_agent, application_modules
    ):
        """A borrowed agent survives cleanup; its guidelines must not re-create."""
        mock_parlant_server.container = {application_modules: MagicMock()}
        adapter = ParlantAdapter(
            server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )
        adapter.add_guideline(condition="c", action="a")

        await adapter.on_started("BandName", "Band description")
        await adapter.cleanup_all()
        await adapter.on_started("BandName", "Band description")

        assert mock_parlant_agent.create_guideline.await_count == 1

    async def test_restart_with_owned_server_applies_guidelines_to_fresh_agent(
        self, owned_server, mock_parlant_agent
    ):
        adapter = ParlantAdapter(name="X", description="Y")
        adapter.add_guideline(condition="c", action="a")

        await adapter.on_started("BandName", "Band description")
        await adapter.cleanup_all()
        await adapter.on_started("BandName", "Band description")

        assert mock_parlant_agent.create_guideline.await_count == 2

    async def test_on_started_failure_leaves_cleanup_to_server_context(
        self, owned_server
    ):
        _, cm, _ = owned_server

        async def configure(srv, agent):
            raise RuntimeError("configure blew up")

        adapter = ParlantAdapter(name="X", description="Y", configure=configure)

        with pytest.raises(RuntimeError, match="configure blew up"):
            await adapter.on_started("BandName", "Band description")

        # A context manager whose __aenter__ raises owns its partial-enter cleanup;
        # calling __aexit__ again from the adapter would double-close it.
        cm.__aexit__.assert_not_awaited()
        assert adapter._server is None


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.fixture
    def initialized_adapter(self, mock_parlant_server, mock_parlant_agent):
        """Create an initialized adapter with mocked app."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            response_timeout=0.05,
            response_poll=0.01,
        )
        adapter.agent_name = "TestBot"
        adapter.agent_description = "A test bot"

        # Mock the application
        mock_app = MagicMock()
        mock_app.sessions = AsyncMock()
        mock_app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
        mock_app.sessions.create_customer_message = AsyncMock(
            return_value=MagicMock(offset=1)
        )
        mock_app.sessions.wait_for_more_events = AsyncMock(return_value=False)
        mock_app.sessions.find_events = AsyncMock(return_value=[])

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
                    EventSource=MagicMock(CUSTOMER="customer")
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
    async def test_customer_id_does_not_collide_across_rooms_sharing_a_prefix(
        self, initialized_adapter, mock_parlant_server
    ):
        """Two rooms sharing a UUID prefix must map to distinct Parlant customers."""
        room_a = "aaaaaaaa-1111-4444-8888-000000000001"
        room_b = "aaaaaaaa-2222-4444-8888-000000000002"

        await initialized_adapter._get_or_create_customer(room_a, "Alice")
        await initialized_adapter._get_or_create_customer(room_b, "Bob")

        customer_ids = [
            call.kwargs["id"]
            for call in mock_parlant_server.create_customer.await_args_list
        ]
        assert customer_ids == [f"band-{room_a}", f"band-{room_b}"]
        assert len(set(customer_ids)) == 2

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
            # First call sets the tools with session_id
            mock_set_tools.assert_any_call("session-123", mock_tools)
            # Second call clears the tools
            mock_set_tools.assert_any_call("session-123", None)

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

        # Should return early without error
        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # No calls should be made
        mock_tools.send_message.assert_not_called()


class TestResponseWaitBudget:
    """The response wait retries across empty poll windows up to a total budget.

    A cold start (server warmup + the first NLP round-trips) can leave the first
    poll window empty on a slow host. The wait must keep polling until the budget,
    not abandon the turn after one empty window — otherwise a slow first turn is
    silently dropped (no reply forwarded). This is the deterministic guard for that
    behavior: the live E2E can only trigger it on a genuinely slow runner, so it
    can't reliably reproduce it, but driving the wait loop directly can.
    """

    @staticmethod
    def _agent_event(message: str, offset: int, tags: list[str] | None = None):
        """A Parlant AI-agent MESSAGE event as the wait loop reads it."""
        from parlant.core.sessions import EventKind, EventSource

        event = MagicMock()
        event.kind = EventKind.MESSAGE
        event.source = EventSource.AI_AGENT
        event.offset = offset
        event.data = {"message": message, "tags": tags or []}
        return event

    @staticmethod
    def _app_with_waits(wait_results, events):
        """A mock Application whose wait_for_more_events yields ``wait_results`` in
        order and whose find_events returns ``events``."""
        app = MagicMock()
        app.sessions = AsyncMock()
        app.sessions.wait_for_more_events = AsyncMock(side_effect=wait_results)
        app.sessions.find_events = AsyncMock(return_value=events)
        return app

    @pytest.mark.asyncio
    async def test_retries_past_empty_windows_then_forwards_late_reply(
        self, mock_parlant_server, mock_parlant_agent, mock_tools
    ):
        """Two empty poll windows (still generating), then the reply arrives — it is
        still forwarded, not dropped after the first empty window."""
        adapter = ParlantAdapter(
            server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )
        adapter._app = self._app_with_waits(
            wait_results=[False, False, True],
            events=[self._agent_event("Hello there!", offset=2)],
        )

        await adapter._process_agent_response(
            session_id="cold-start-session",
            room_id="room-1",
            min_offset=0,
            tools=mock_tools,
            sender_name="Alice",
        )

        # The reply is only returned on the 3rd wait, so forwarding it proves the loop
        # retried past both empty windows instead of giving up on the first.
        mock_tools.send_message.assert_awaited_once_with(
            "Hello there!", mentions=["Alice"]
        )

    @pytest.mark.asyncio
    async def test_gives_up_after_budget_when_no_reply_ever_arrives(
        self, mock_parlant_server, mock_parlant_agent, mock_tools
    ):
        """A genuinely silent turn is bounded: once the total budget elapses the wait
        returns (no hang) and nothing is forwarded."""
        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            response_timeout=0.05,
            response_poll=0.01,
        )
        # Never any event: every poll window is empty.
        app = MagicMock()
        app.sessions = AsyncMock()
        app.sessions.wait_for_more_events = AsyncMock(return_value=False)
        app.sessions.find_events = AsyncMock(return_value=[])
        adapter._app = app

        await asyncio.wait_for(
            adapter._process_agent_response(
                session_id="silent-session",
                room_id="room-1",
                min_offset=0,
                tools=mock_tools,
                sender_name="Alice",
            ),
            timeout=5,
        )

        # It gave up within the budget (the wait_for above would raise on a hang)
        # without forwarding anything.
        mock_tools.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preamble_only_times_out_without_forwarding_a_reply(
        self, mock_parlant_server, mock_parlant_agent, mock_tools
    ):
        """Parlant emits a preamble then stalls the final generation. A preamble is an
        acknowledgment, not an answer, so the adapter must NOT forward it as the reply
        — the turn is given up honestly (no send_message) rather than faking success."""
        from band.adapters.parlant import PARLANT_PREAMBLE_TAG

        adapter = ParlantAdapter(
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
            response_timeout=0.05,
            response_poll=0.01,
        )
        # The preamble arrives on the first poll; no final ever follows.
        seen = {"delivered": False}

        def _preamble_once(*_args, **_kwargs):
            if seen["delivered"]:
                return False
            seen["delivered"] = True
            return True

        app = MagicMock()
        app.sessions = AsyncMock()
        app.sessions.wait_for_more_events = AsyncMock(side_effect=_preamble_once)
        app.sessions.find_events = AsyncMock(
            return_value=[
                self._agent_event("One moment…", offset=1, tags=[PARLANT_PREAMBLE_TAG])
            ]
        )
        adapter._app = app

        await asyncio.wait_for(
            adapter._process_agent_response(
                session_id="preamble-only-session",
                room_id="room-1",
                min_offset=0,
                tools=mock_tools,
                sender_name="Alice",
            ),
            timeout=5,
        )

        # The preamble was NOT forwarded — a stalled turn fails honestly, not silently
        # dressed up as an answer.
        mock_tools.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_find_events_then_final_still_forwards(
        self, mock_parlant_server, mock_parlant_agent, mock_tools
    ):
        """A positive wait signal with an empty find_events read is a transient
        visibility gap, not the answer: the loop keeps polling and forwards the final
        message once it becomes query-visible, rather than dropping the turn."""
        adapter = ParlantAdapter(
            server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )
        app = MagicMock()
        app.sessions = AsyncMock()
        # The signal fires both reads; the event is only query-visible on the second.
        app.sessions.wait_for_more_events = AsyncMock(return_value=True)
        app.sessions.find_events = AsyncMock(
            side_effect=[[], [self._agent_event("The answer.", offset=2)]]
        )
        adapter._app = app

        await adapter._process_agent_response(
            session_id="visibility-gap-session",
            room_id="room-1",
            min_offset=0,
            tools=mock_tools,
            sender_name="Alice",
        )

        # The final is only query-visible on the 2nd read, so forwarding it proves the
        # loop re-polled past the empty read instead of dropping the turn.
        mock_tools.send_message.assert_awaited_once_with(
            "The answer.", mentions=["Alice"]
        )
