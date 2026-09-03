"""Parameterized conformance tests for all framework adapters.

These tests verify the shared behavioral contract across all six framework
adapters. Framework-specific behavior (tool routing, stream handling, etc.)
remains in the per-framework test files under tests/adapters/.
"""

from __future__ import annotations

import pytest

from band.core.types import Capability
from tests.baseline.adapter import Adapter
from tests.e2e.baseline.agents import ExcludedAdapter


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
    construction-time validation in SimpleAdapter.__init__().
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

    def test_features_kwargs_accepted(self, adapter_config):
        """Every adapter constructor must accept emit=/capabilities=/... directly."""
        adapter = adapter_config.adapter_factory(emit=(), capabilities=())
        assert adapter.features.emit == frozenset()
        assert adapter.features.capabilities == frozenset()

    def test_omitted_emit_defaults_to_supported_emit(self, adapter_config):
        """Omitting emit= narrates everything the adapter declares support for."""
        adapter = adapter_config.adapter_factory()
        assert adapter.features.emit == type(adapter).SUPPORTED_EMIT

    def test_declared_capabilities_accepted_at_construction(self, adapter_config):
        """Every capability an adapter claims via SUPPORTED_CAPABILITIES must
        actually be accepted, not just listed -- guards against a stale
        SUPPORTED_CAPABILITIES entry whose wiring to the schema-building call
        site was removed or never passed self.features.capabilities through.
        """
        cls = adapter_config.adapter_factory().__class__
        adapter = adapter_config.adapter_factory(
            capabilities=cls.SUPPORTED_CAPABILITIES
        )
        assert adapter.features.capabilities == cls.SUPPORTED_CAPABILITIES


class TestFilesCapabilityMatrix:
    """Every registered adapter accepts Capability.FILES.

    Registry-driven adapters get the file tools generically (the shared
    registry in band.runtime.tools); parlant, pydantic_ai and
    crewai/crewai_flow hand-roll one wrapper per platform tool and grew real
    band_list_room_files/band_read_room_file/band_send_room_file wrappers to
    match. A new adapter that skips that work fails here.
    """

    def test_accepts_files_capability(self, adapter_config):
        adapter = adapter_config.adapter_factory(capabilities=Capability.FILES)

        assert Capability.FILES in adapter.features.capabilities


# Image passthrough (a band_read_room_file image result reaching the model as
# real vision content, not a json.dumps'd text block) is the default
# expectation; these are the adapters that deliberately don't have it, and
# why. Stating the exceptions rather than the 12 that comply means a newly
# registered adapter is expected to comply, and the probe-registry guard in
# test_files_image_passthrough_matrix fails loudly until it does.
#
# Two distinct kinds of exception, deliberately not flattened together:
#   - a framework that *cannot* carry image content at all, and
#   - one that gets passthrough elsewhere, so has no adapter-local path to probe.
IMAGE_PASSTHROUGH_EXCLUSIONS = (
    ExcludedAdapter(
        Adapter.GOOGLE_ADK,
        "google-adk's own __build_response_event() calls "
        "Part.from_function_response() without a parts= kwarg, so no tool "
        "return value can reach a multimodal FunctionResponse -- a framework "
        "limitation, not an adapter gap",
    ),
    ExcludedAdapter(
        Adapter.LETTA,
        "routes tool execution through the shared MCP engine rather than "
        "execute_tool_call, so it inherits opencode's fix and has no "
        "adapter-local path to probe",
    ),
    ExcludedAdapter(
        Adapter.COPILOT_ACP,
        "wraps the ACP client adapter, which shares the same MCP engine fix; "
        "not a separate ADAPTER_CONFIGS entry, so it has no probe of its own",
    ),
)

# parlant is absent from the Adapter enum entirely (NON_AGENT_ADAPTERS), so it
# can't be listed above -- but it is equally unsupportable:
# parlant.core.tools.ToolResult has no multimodal field, and Parlant's own MCP
# integration (mcp_result_to_tool_result_data()) discards image content blocks
# before they ever reach ToolResult.data.
#
# Derived from the Adapter enum, not ADAPTER_CONFIGS: the enum is a plain
# StrEnum that every lane sees identically, while ADAPTER_CONFIGS silently
# drops configs whose framework isn't installed (9 of 15 under dev-crewai /
# dev-parlant), which would make this constant mean different things per lane.
IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS = frozenset(
    adapter.value for adapter in Adapter
) - {excluded.adapter.value for excluded in IMAGE_PASSTHROUGH_EXCLUSIONS}

# Where each supported adapter's behavioural proof lives. The mechanism differs
# per framework, so it can't be exercised generically through ADAPTER_CONFIGS --
# test_files_image_passthrough_matrix drives one probe per adapter instead.
#   claude_sdk    tests/integrations/claude_sdk/test_tools.py (MCP passthrough)
#   anthropic     test_anthropic_adapter.py (ImageBlockParam)
#   opencode      tests/mcp/test_engine.py (the shared MCP engine's ImageContent)
#   gemini        test_gemini_adapter.py (FunctionResponseBlob inline_data)
#   langgraph     test_langchain_tools.py (langchain_core ImageContentBlock)
#   agno          tests/adapters/agno/test_adapter.py (agno.media.Image)
#   strands       test_strands_adapter.py (ToolResultContent image block)
#   copilot_sdk   copilot_sdk/test_tool_bridging.py (ToolBinaryResult)
#   codex         test_codex_adapter.py ("inputImage" content item -- wire-shape
#                 verified against codex-cli's generated schema; whether
#                 codex-rs accepts the data: URI isn't checkable from Python)
#   pydantic_ai   test_pydantic_ai_adapter.py (messages.BinaryContent)
#   crewai(_flow) test_crewai_tools.py (CrewAI's VISION_IMAGE: sentinel, parsed
#                 by StepExecutor; both adapters share one vision_sentinel())


class TestImagePassthroughMatrix:
    """Registry-consistency guard for the image-passthrough expectation.

    Guards the bookkeeping only -- that every adapter expected to support
    image passthrough is a real registered adapter. The behavioural proof
    lives in the per-framework probes (see the citations above).
    """

    def test_every_supported_id_is_a_registered_adapter(self) -> None:
        known_ids = {adapter.value for adapter in Adapter}

        assert IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS <= known_ids

    def test_every_exclusion_carries_a_reason(self) -> None:
        assert all(excluded.reason for excluded in IMAGE_PASSTHROUGH_EXCLUSIONS)

    def test_e2e_matrix_list_matches_this_unit_level_set(self) -> None:
        """IMAGE_PASSTHROUGH_ADAPTERS (the E2E matrix's own adapter list,
        tests/e2e/baseline/smoke/matrix/test_capability_matrix.py) must stay
        derived from this set, not hand-maintained separately -- otherwise a
        future adapter gaining image-passthrough support here could leave
        the E2E smoke silently never added for it, and CI would stay green
        while coverage quietly regressed. The two lists differ by design
        (see the comment above IMAGE_PASSTHROUGH_ADAPTERS): -crewai_flow (no
        Band tool loop to drive a file round-trip), +copilot_acp and +letta
        (both share opencode's already-fixed MCP engine, so they're excluded
        from *this* unit-level set as having no probe of their own, but get
        a real E2E cell each)."""
        from tests.e2e.baseline.smoke.matrix.test_capability_matrix import (
            IMAGE_PASSTHROUGH_ADAPTERS,
        )

        expected = (
            IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS - {Adapter.CREWAI_FLOW.value}
        ) | {Adapter.COPILOT_ACP.value, Adapter.LETTA.value}

        assert {a.value for a in IMAGE_PASSTHROUGH_ADAPTERS} == expected
