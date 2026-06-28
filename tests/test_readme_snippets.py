"""Smoke tests for every code snippet in README.md.

Validates that import paths, constructor signatures, parameter names,
and class hierarchies shown in the README actually work against the real SDK.
These tests do NOT call LLMs or the live platform.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

try:
    from band.adapters import PydanticAIAdapter as _PydanticAICheck  # noqa: F401

    _has_pydantic_ai = True
except (ImportError, Exception):
    _has_pydantic_ai = False

try:
    import claude_code_sdk  # noqa: F401

    _has_claude_sdk = True
except (ImportError, Exception):
    _has_claude_sdk = False

skip_no_pydantic_ai = pytest.mark.skipif(
    not _has_pydantic_ai, reason="pydantic-ai not installed or broken"
)
skip_no_claude_sdk = pytest.mark.skipif(
    not _has_claude_sdk, reason="claude-agent-sdk not installed"
)


# ---------------------------------------------------------------------------
# Section: Install / top-level imports
# ---------------------------------------------------------------------------


class TestTopLevelImports:
    """README shows `from band import Agent` and similar."""

    def test_agent_import(self) -> None:
        from band import Agent, build_logging_config, configure_logging

        assert Agent is not None
        assert build_logging_config is not None
        assert configure_logging is not None

    def test_adapter_features_and_capability_import(self) -> None:
        from band.core.types import AdapterFeatures, Capability

        assert AdapterFeatures is not None
        assert Capability is not None

    def test_adapter_features_shorthand_import(self) -> None:
        """README uses `from band import AdapterFeatures, Emit`."""
        from band import AdapterFeatures, Emit

        assert AdapterFeatures is not None
        assert Emit is not None

    def test_capability_shorthand_import(self) -> None:
        """README uses `from band import Capability, Emit`."""
        from band import Capability, Emit

        assert Capability is not None
        assert Emit is not None

    def test_exception_imports(self) -> None:
        from band import (
            BandConfigError,
            BandConnectionError,
            BandError,
            BandToolError,
        )

        assert issubclass(BandConfigError, BandError)
        assert issubclass(BandConnectionError, BandError)
        assert issubclass(BandToolError, BandError)


# ---------------------------------------------------------------------------
# Section: Quickstart – LangGraph simple pattern
# ---------------------------------------------------------------------------


class TestQuickstartLangGraph:
    """README quickstart shows LangGraphAdapter(llm=..., checkpointer=...)."""

    def test_adapter_import(self) -> None:
        from band.adapters import LangGraphAdapter

        assert LangGraphAdapter is not None

    @patch.dict(
        os.environ,
        {
            "QUICKSTART_AGENT_ID": "test-agent-id",
            "QUICKSTART_API_KEY": "test-api-key",
        },
    )
    def test_quickstart_instantiation(self) -> None:
        from band import Agent
        from band.adapters import LangGraphAdapter

        llm = MagicMock()
        checkpointer = MagicMock()

        adapter = LangGraphAdapter(llm=llm, checkpointer=checkpointer)

        agent = Agent.create(
            adapter=adapter,
            agent_id=os.environ["QUICKSTART_AGENT_ID"],
            api_key=os.environ["QUICKSTART_API_KEY"],
        )

        assert agent is not None


# ---------------------------------------------------------------------------
# Section: Same Pattern, Any Framework – adapter swap snippets
# ---------------------------------------------------------------------------


class TestAdapterSwapSnippets:
    """README shows short adapter-swap snippets for Anthropic, PydanticAI, Gemini."""

    def test_anthropic_adapter_import_and_init(self) -> None:
        from band.adapters import AnthropicAdapter

        adapter = AnthropicAdapter(model="claude-sonnet-4-5")
        assert adapter is not None

    @skip_no_pydantic_ai
    def test_pydantic_ai_adapter_import_and_init(self) -> None:
        from band.adapters import PydanticAIAdapter

        adapter = PydanticAIAdapter(model="openai:gpt-5.4-mini")
        assert adapter is not None

    def test_gemini_adapter_import_and_init(self) -> None:
        from band.adapters import GeminiAdapter

        adapter = GeminiAdapter(model="gemini-2.5-flash")
        assert adapter is not None


# ---------------------------------------------------------------------------
# Section: Supported Adapters table – all adapters importable
# ---------------------------------------------------------------------------


class TestSupportedAdaptersTable:
    """README table lists every adapter with its import path."""

    def test_langgraph_adapter(self) -> None:
        from band.adapters import LangGraphAdapter

        assert LangGraphAdapter is not None

    @skip_no_pydantic_ai
    def test_pydantic_ai_adapter(self) -> None:
        from band.adapters import PydanticAIAdapter

        assert PydanticAIAdapter is not None

    def test_anthropic_adapter(self) -> None:
        from band.adapters import AnthropicAdapter

        assert AnthropicAdapter is not None

    @skip_no_claude_sdk
    def test_claude_sdk_adapter(self) -> None:
        from band.adapters import ClaudeSDKAdapter

        assert ClaudeSDKAdapter is not None

    def test_crewai_adapter(self) -> None:
        from band.adapters import CrewAIAdapter

        assert CrewAIAdapter is not None

    def test_crewai_flow_adapter(self) -> None:
        from band.adapters import CrewAIFlowAdapter

        assert CrewAIFlowAdapter is not None

    def test_gemini_adapter(self) -> None:
        from band.adapters import GeminiAdapter

        assert GeminiAdapter is not None

    def test_google_adk_adapter(self) -> None:
        from band.adapters import GoogleADKAdapter

        assert GoogleADKAdapter is not None

    def test_parlant_adapter(self) -> None:
        from band.adapters import ParlantAdapter

        assert ParlantAdapter is not None

    def test_letta_adapter(self) -> None:
        from band.adapters import LettaAdapter

        assert LettaAdapter is not None

    def test_codex_adapter(self) -> None:
        from band.adapters import CodexAdapter

        assert CodexAdapter is not None

    def test_opencode_adapter(self) -> None:
        from band.adapters import OpencodeAdapter

        assert OpencodeAdapter is not None

    def test_a2a_adapter(self) -> None:
        from band.adapters.a2a import A2AAdapter, A2AAuth

        assert A2AAdapter is not None
        assert A2AAuth is not None

    def test_a2a_gateway_adapter(self) -> None:
        from band.adapters.a2a_gateway import A2AGatewayAdapter

        assert A2AGatewayAdapter is not None

    def test_acp_client_adapter(self) -> None:
        from band.adapters.acp import ACPClientAdapter

        assert ACPClientAdapter is not None


# ---------------------------------------------------------------------------
# Section: Platform Tools – AdapterFeatures / Capability
# ---------------------------------------------------------------------------


class TestPlatformToolsSnippets:
    """README shows AdapterFeatures with Capability and Emit."""

    def test_capability_set_creation(self) -> None:
        from band.core.types import AdapterFeatures, Capability

        features = AdapterFeatures(
            capabilities={Capability.CONTACTS, Capability.MEMORY},
        )

        assert Capability.CONTACTS in features.capabilities
        assert Capability.MEMORY in features.capabilities

    def test_adapter_with_features(self) -> None:
        """README snippet: AnthropicAdapter with capabilities."""
        from band.adapters import AnthropicAdapter
        from band.core.types import AdapterFeatures, Capability

        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5",
            features=AdapterFeatures(
                capabilities={Capability.CONTACTS, Capability.MEMORY},
            ),
        )

        assert Capability.CONTACTS in adapter.features.capabilities
        assert Capability.MEMORY in adapter.features.capabilities


# ---------------------------------------------------------------------------
# Section: Emit Options
# ---------------------------------------------------------------------------


class TestEmitOptionsSnippets:
    """README shows emit configuration on adapters."""

    def test_emit_enum_values(self) -> None:
        from band import Emit

        assert hasattr(Emit, "EXECUTION")
        assert hasattr(Emit, "THOUGHTS")
        assert hasattr(Emit, "TASK_EVENTS")

    def test_anthropic_with_emit(self) -> None:
        """README snippet: AdapterFeatures(emit={Emit.EXECUTION})."""
        from band import AdapterFeatures, Emit
        from band.adapters import AnthropicAdapter

        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5",
            features=AdapterFeatures(
                emit={Emit.EXECUTION},
            ),
        )

        assert Emit.EXECUTION in adapter.features.emit

    @skip_no_claude_sdk
    def test_claude_sdk_with_emit_and_capability(self) -> None:
        """README snippet: capabilities + emit combined."""
        from band import AdapterFeatures, Capability, Emit
        from band.adapters import ClaudeSDKAdapter

        adapter = ClaudeSDKAdapter(
            model="sonnet",
            features=AdapterFeatures(
                capabilities={Capability.MEMORY},
                emit={Emit.EXECUTION, Emit.THOUGHTS},
            ),
        )

        assert Capability.MEMORY in adapter.features.capabilities
        assert Emit.EXECUTION in adapter.features.emit
        assert Emit.THOUGHTS in adapter.features.emit

    def test_codex_all_emits(self) -> None:
        """README snippet: all three emit options on CodexAdapter."""
        from band import AdapterFeatures, Emit
        from band.adapters import CodexAdapter

        adapter = CodexAdapter(
            features=AdapterFeatures(
                emit={Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS},
            ),
        )

        assert Emit.EXECUTION in adapter.features.emit
        assert Emit.THOUGHTS in adapter.features.emit
        assert Emit.TASK_EVENTS in adapter.features.emit


# ---------------------------------------------------------------------------
# Section: Custom Instructions
# ---------------------------------------------------------------------------


class TestCustomInstructionsSnippets:
    """README shows custom_section and prompt params."""

    def test_langgraph_custom_section(self) -> None:
        from band.adapters import LangGraphAdapter

        llm = MagicMock()
        checkpointer = MagicMock()

        adapter = LangGraphAdapter(
            llm=llm,
            checkpointer=checkpointer,
            custom_section=(
                "You are a support triage agent. Ask concise clarifying questions, "
                "summarize decisions, and mention the right specialist when needed."
            ),
        )

        assert "support triage" in adapter.custom_section

    def test_anthropic_prompt(self) -> None:
        from band.adapters import AnthropicAdapter

        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5",
            prompt="You are a concise technical reviewer. Focus on risks and next steps.",
        )

        assert adapter is not None


# ---------------------------------------------------------------------------
# Section: Custom Tools
# ---------------------------------------------------------------------------


class TestCustomToolsSnippets:
    """README shows Pydantic model + callable for custom tools."""

    def test_anthropic_custom_tools(self) -> None:
        from band.adapters import AnthropicAdapter

        class WeatherInput(BaseModel):
            """Get current weather for a city."""

            city: str = Field(description="City name")

        def get_weather(args: WeatherInput) -> str:
            return f"Sunny, 22 C in {args.city}"

        adapter = AnthropicAdapter(
            model="claude-sonnet-4-5",
            additional_tools=[(WeatherInput, get_weather)],
        )

        assert adapter is not None


# ---------------------------------------------------------------------------
# Section: Bring Your Own Agent – graph_factory
# ---------------------------------------------------------------------------


class TestBYOASnippet:
    """README shows graph_factory pattern for LangGraph."""

    def test_graph_factory_pattern(self) -> None:
        from band.adapters import LangGraphAdapter

        _llm = MagicMock()
        _checkpointer = MagicMock()
        _my_tools: list = []

        def graph_factory(band_tools):
            mock_graph = MagicMock()
            return mock_graph

        adapter = LangGraphAdapter(graph_factory=graph_factory)

        assert adapter.graph_factory is not None


# ---------------------------------------------------------------------------
# Section: Contact Management
# ---------------------------------------------------------------------------


class TestContactManagementSnippets:
    """README shows ContactEventConfig with HUB_ROOM and CALLBACK strategies."""

    def test_contact_event_imports(self) -> None:
        from band.runtime.types import ContactEventStrategy

        assert ContactEventStrategy.DISABLED is not None
        assert ContactEventStrategy.HUB_ROOM is not None
        assert ContactEventStrategy.CALLBACK is not None

    @patch.dict(
        os.environ,
        {
            "QUICKSTART_AGENT_ID": "test-agent-id",
            "QUICKSTART_API_KEY": "test-api-key",
        },
    )
    def test_hub_room_config(self) -> None:
        """README snippet: Agent.create with HUB_ROOM strategy."""
        from band import Agent
        from band.runtime.types import ContactEventConfig, ContactEventStrategy

        adapter = MagicMock()

        agent = Agent.create(
            adapter=adapter,
            agent_id=os.environ["QUICKSTART_AGENT_ID"],
            api_key=os.environ["QUICKSTART_API_KEY"],
            contact_config=ContactEventConfig(
                strategy=ContactEventStrategy.HUB_ROOM,
            ),
        )

        assert agent is not None

    @patch.dict(
        os.environ,
        {
            "QUICKSTART_AGENT_ID": "test-agent-id",
            "QUICKSTART_API_KEY": "test-api-key",
        },
    )
    def test_callback_config(self) -> None:
        """README snippet: Agent.create with CALLBACK strategy + handler."""
        from band import Agent
        from band.platform.event import ContactRequestReceivedEvent
        from band.runtime.types import ContactEventConfig, ContactEventStrategy

        TRUSTED_HANDLES = {"@teammate"}

        async def handle_contact(event, tools) -> None:
            if not isinstance(event, ContactRequestReceivedEvent):
                return
            action = (
                "approve" if event.payload.from_handle in TRUSTED_HANDLES else "reject"
            )
            await tools.respond_contact_request(action, request_id=event.payload.id)

        adapter = MagicMock()

        agent = Agent.create(
            adapter=adapter,
            agent_id=os.environ["QUICKSTART_AGENT_ID"],
            api_key=os.environ["QUICKSTART_API_KEY"],
            contact_config=ContactEventConfig(
                strategy=ContactEventStrategy.CALLBACK,
                on_event=handle_contact,
            ),
        )

        assert agent is not None

    def test_contact_request_payload_fields(self) -> None:
        """Verify payload has from_handle and id fields."""
        from band.client.streaming import ContactRequestReceivedPayload

        payload = ContactRequestReceivedPayload(
            id="req-1",
            from_handle="@alice",
            from_name="Alice",
            status="pending",
            inserted_at="2025-01-01T00:00:00Z",
        )

        assert payload.id == "req-1"
        assert payload.from_handle == "@alice"


# ---------------------------------------------------------------------------
# Section: Protocol Bridges – A2A
# ---------------------------------------------------------------------------


class TestA2ABridgeSnippet:
    """README snippet: A2AAdapter(remote_url=..., auth=...)."""

    def test_a2a_adapter_instantiation(self) -> None:
        from band.adapters.a2a import A2AAdapter, A2AAuth

        adapter = A2AAdapter(
            remote_url="http://localhost:10000",
            auth=A2AAuth(api_key="test-key"),
        )

        assert adapter.remote_url == "http://localhost:10000"


# ---------------------------------------------------------------------------
# Section: Protocol Bridges – A2A Gateway
# ---------------------------------------------------------------------------


class TestA2AGatewaySnippet:
    """README snippet: A2AGatewayAdapter(api_key=..., gateway_url=..., port=...)."""

    @patch.dict(
        os.environ,
        {
            "GATEWAY_AGENT_ID": "test-agent-id",
            "GATEWAY_API_KEY": "test-api-key",
        },
    )
    def test_gateway_full_snippet(self) -> None:
        from band import Agent
        from band.adapters.a2a_gateway import A2AGatewayAdapter

        gateway_port = int(os.getenv("GATEWAY_PORT", "10000"))
        gateway_url = os.getenv("GATEWAY_URL", f"http://localhost:{gateway_port}")

        adapter = A2AGatewayAdapter(
            api_key=os.environ["GATEWAY_API_KEY"],
            gateway_url=gateway_url,
            port=gateway_port,
        )

        agent = Agent.create(
            adapter=adapter,
            agent_id=os.environ["GATEWAY_AGENT_ID"],
            api_key=os.environ["GATEWAY_API_KEY"],
        )

        assert agent is not None


# ---------------------------------------------------------------------------
# Section: Exceptions hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """README states BandError is the base for the other three."""

    def test_hierarchy(self) -> None:
        from band import (
            BandConfigError,
            BandConnectionError,
            BandError,
            BandToolError,
        )

        assert issubclass(BandConfigError, BandError)
        assert issubclass(BandConnectionError, BandError)
        assert issubclass(BandToolError, BandError)

    def test_exceptions_are_raiseable(self) -> None:
        from band import BandConfigError, BandConnectionError, BandToolError

        with pytest.raises(BandConfigError):
            raise BandConfigError("bad config")

        with pytest.raises(BandConnectionError):
            raise BandConnectionError("connection lost")

        with pytest.raises(BandToolError):
            raise BandToolError("tool failed")


# ---------------------------------------------------------------------------
# Section: Quick Reference – Agent.create pattern
# ---------------------------------------------------------------------------


class TestQuickReferenceSnippets:
    """README quick reference table shows connect/create patterns."""

    @patch.dict(
        os.environ,
        {
            "AGENT_ID": "test-agent-id",
            "API_KEY": "test-api-key",
        },
    )
    def test_agent_create_and_run_signature(self) -> None:
        from band import Agent

        adapter = MagicMock()

        agent = Agent.create(
            adapter=adapter,
            agent_id=os.environ["AGENT_ID"],
            api_key=os.environ["API_KEY"],
        )

        assert hasattr(agent, "run")
