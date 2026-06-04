"""Parameterized conformance tests for all framework adapters.

These tests verify the shared behavioral contract across all six framework
adapters. Framework-specific behavior (tool routing, stream handling, etc.)
remains in the per-framework test files under tests/adapters/.
"""

from __future__ import annotations

import pytest


class TestAdapterConfigIntegrity:
    """Validate that AdapterConfig registries stay in sync with adapter source."""

    def test_expected_initial_values_attrs_exist(self, adapter_config):
        """Every key in expected_initial_values must exist as an adapter attribute.

        Catches drift when an adapter renames or removes an __init__ parameter
        (e.g. migration to attrs/Pydantic) but the config is not updated.
        """
        adapter = adapter_config.adapter_factory()

        for attr_name in adapter_config.expected_initial_values:
            assert hasattr(adapter, attr_name), (
                f"{adapter_config.display_name}: expected_initial_values references "
                f"{attr_name!r} but the adapter has no such attribute. "
                f"Update the AdapterConfig or the adapter __init__."
            )

    def test_custom_expected_attrs_exist(self, adapter_config):
        """Every key in custom_expected must exist after custom initialization."""
        if not adapter_config.custom_kwargs:
            pytest.skip(f"{adapter_config.display_name} has no custom kwargs")

        adapter = adapter_config.adapter_factory(**adapter_config.custom_kwargs)

        for attr_name in adapter_config.custom_expected:
            assert hasattr(adapter, attr_name), (
                f"{adapter_config.display_name}: custom_expected references "
                f"{attr_name!r} but the adapter has no such attribute. "
                f"Update the AdapterConfig or the adapter __init__."
            )


class TestAdapterInitialization:
    """All adapters share common initialization patterns."""

    def test_default_initialization(self, adapter_config):
        """Adapter defaults match expected values.

        For PydanticAI, ``expected_initial_values["model"]`` verifies that the
        conformance factory injects the model kwarg correctly, not a real
        adapter default (the adapter has no default for model).
        """
        adapter = adapter_config.adapter_factory()

        for attr_name, expected in adapter_config.expected_initial_values.items():
            actual = getattr(adapter, attr_name)
            assert actual == expected, (
                f"{adapter_config.display_name}.{attr_name}: "
                f"expected {expected!r}, got {actual!r}"
            )

    def test_custom_initialization(self, adapter_config):
        """Adapter accepts and stores custom kwargs."""
        if not adapter_config.custom_kwargs:
            pytest.skip(f"{adapter_config.display_name} has no custom kwargs to test")

        adapter = adapter_config.adapter_factory(**adapter_config.custom_kwargs)

        for attr_name, expected in adapter_config.custom_expected.items():
            actual = getattr(adapter, attr_name)
            assert actual == expected, (
                f"{adapter_config.display_name}.{attr_name}: "
                f"expected {expected!r}, got {actual!r}"
            )

    def test_defaults_to_empty_custom_tools(self, adapter_config):
        """Adapters with custom tools start with an empty list."""
        if not adapter_config.has_custom_tools_attr:
            pytest.skip(
                f"{adapter_config.display_name} does not support custom tools attribute"
            )

        adapter = adapter_config.adapter_factory()
        tools = getattr(adapter, adapter_config.custom_tools_attr)

        assert tools == []

    def test_has_history_converter(self, adapter_config):
        """Adapters have a history_converter attribute."""
        if not adapter_config.has_history_converter:
            pytest.skip(
                f"{adapter_config.display_name} does not expose history_converter"
            )

        adapter = adapter_config.adapter_factory()

        assert adapter.history_converter is not None


class TestAdapterOnStarted:
    """All adapters set agent name and description after on_started."""

    @pytest.mark.asyncio
    async def test_after_on_started_sets_agent_name_and_description(
        self, adapter_config
    ):
        """After on_started(agent_name, agent_description), adapter has them set."""
        if adapter_config.skip_on_started_conformance:
            pytest.skip(
                f"{adapter_config.display_name} on_started requires live client (tested in framework-specific tests)"
            )
        adapter = adapter_config.adapter_factory()
        await adapter.on_started(
            agent_name="TestBot",
            agent_description="A test bot for conformance.",
        )

        assert adapter.agent_name == "TestBot"
        assert adapter.agent_description == "A test bot for conformance."


class TestAdapterOnMessage:
    """All adapters expose an on_message method with the expected signature."""

    def test_on_message_is_callable(self, adapter_config):
        """Adapter has a callable on_message method."""
        adapter = adapter_config.adapter_factory()
        assert hasattr(adapter, "on_message")
        assert callable(adapter.on_message)

    def test_on_message_is_coroutine_function(self, adapter_config):
        """on_message must be an async method."""
        import inspect

        adapter = adapter_config.adapter_factory()
        assert inspect.iscoroutinefunction(adapter.on_message)


class TestAdapterCleanup:
    """All adapters handle cleanup safely."""

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_room_is_safe(self, adapter_config):
        """Cleaning up a room that was never used should not raise."""
        adapter = adapter_config.adapter_factory()

        # Should not raise
        await adapter.on_cleanup("nonexistent-room")


class TestAdapterFeaturesContract:
    """Every adapter must declare its supported emit/capability set.

    Catches regressions where a new adapter is added without declaring
    SUPPORTED_EMIT or SUPPORTED_CAPABILITIES — which would break the
    feature warning mechanism in SimpleAdapter.on_started().
    """

    def test_supported_emit_declared(self, adapter_config):
        """Every adapter class must define SUPPORTED_EMIT as a frozenset."""
        from band.core.types import Emit

        adapter = adapter_config.adapter_factory()
        cls = type(adapter)
        assert hasattr(cls, "SUPPORTED_EMIT"), (
            f"{adapter_config.display_name}: missing SUPPORTED_EMIT class var. "
            f"Declare it as `SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset({{...}})`."
        )
        supported = cls.SUPPORTED_EMIT
        assert isinstance(supported, frozenset), (
            f"{adapter_config.display_name}.SUPPORTED_EMIT must be a frozenset, "
            f"got {type(supported).__name__}"
        )
        for value in supported:
            assert isinstance(value, Emit), (
                f"{adapter_config.display_name}.SUPPORTED_EMIT contains non-Emit "
                f"value: {value!r}"
            )

    def test_supported_capabilities_declared(self, adapter_config):
        """Every adapter class must define SUPPORTED_CAPABILITIES as a frozenset."""
        from band.core.types import Capability

        adapter = adapter_config.adapter_factory()
        cls = type(adapter)
        assert hasattr(cls, "SUPPORTED_CAPABILITIES"), (
            f"{adapter_config.display_name}: missing SUPPORTED_CAPABILITIES class var. "
            f"Declare it as "
            f"`SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset({{...}})`."
        )
        supported = cls.SUPPORTED_CAPABILITIES
        assert isinstance(supported, frozenset), (
            f"{adapter_config.display_name}.SUPPORTED_CAPABILITIES must be a frozenset, "
            f"got {type(supported).__name__}"
        )
        for value in supported:
            assert isinstance(value, Capability), (
                f"{adapter_config.display_name}.SUPPORTED_CAPABILITIES contains "
                f"non-Capability value: {value!r}"
            )

    def test_features_param_accepted(self, adapter_config):
        """Every adapter constructor must accept features=AdapterFeatures(...)."""
        from band.core.types import AdapterFeatures

        adapter = adapter_config.adapter_factory(features=AdapterFeatures())
        assert adapter.features == AdapterFeatures()
