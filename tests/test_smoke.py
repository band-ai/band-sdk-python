"""
Smoke tests - verify basic imports and setup work.
"""

from thenvoi import (
    AdapterFeatures,
    AgentRuntime,
    AgentTools,
    Capability,
    Emit,
    ExecutionContext,
    ThenvoiConfigError,
    ThenvoiConnectionError,
    ThenvoiError,
    ThenvoiLink,
    ThenvoiToolError,
)


def test_can_import_runtime():
    """Verify we can import runtime modules."""
    assert ThenvoiLink is not None
    assert AgentRuntime is not None
    assert ExecutionContext is not None
    assert AgentTools is not None


def test_can_import_normalization_types():
    """The v0.3.0 vocabulary types are exposed at the package root."""
    assert Capability is not None
    assert Emit is not None
    assert AdapterFeatures is not None


def test_capability_enum_values():
    """Capability enum exports the expected members."""
    assert Capability.MEMORY == "memory"
    assert Capability.CONTACTS == "contacts"


def test_emit_enum_values():
    """Emit enum exports the expected members."""
    assert Emit.EXECUTION == "execution"
    assert Emit.THOUGHTS == "thoughts"
    assert Emit.TASK_EVENTS == "task_events"


def test_can_import_exception_hierarchy():
    """The four-class exception hierarchy is exposed at the package root."""
    assert ThenvoiError is not None
    assert issubclass(ThenvoiConfigError, ThenvoiError)
    assert issubclass(ThenvoiConnectionError, ThenvoiError)
    assert issubclass(ThenvoiToolError, ThenvoiError)


def test_adapter_features_constructible():
    """AdapterFeatures default-constructs and accepts capabilities."""
    empty = AdapterFeatures()
    assert empty.capabilities == frozenset()
    assert empty.emit == frozenset()

    with_memory = AdapterFeatures(capabilities={Capability.MEMORY})
    assert Capability.MEMORY in with_memory.capabilities


def test_can_import_letta_adapter_via_lazy_loader():
    """LettaAdapter resolves through the adapters lazy loader."""
    from thenvoi.adapters import LettaAdapter, LettaAdapterConfig

    assert LettaAdapter is not None
    assert LettaAdapterConfig is not None


def test_can_import_langgraph_integrations():
    """Verify we can import LangGraph integration utilities."""
    from thenvoi.integrations.langgraph import (
        agent_tools_to_langchain,
        graph_as_tool,
    )

    assert agent_tools_to_langchain is not None
    assert graph_as_tool is not None


def test_fixtures_work(mock_api_client, mock_websocket, sample_room_message):
    """Verify our test fixtures are properly configured."""
    assert mock_api_client is not None
    assert mock_websocket is not None
    assert sample_room_message.chat_room_id == "room-123"
    assert sample_room_message.sender_type == "User"
