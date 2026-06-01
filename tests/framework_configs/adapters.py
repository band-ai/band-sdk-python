"""Adapter configuration registry for parameterized conformance tests.

Each AdapterConfig describes a framework adapter's properties, default values,
custom initialization kwargs, and factory function so that conformance tests can
run identical logic across all registered adapters.
"""

from __future__ import annotations

import functools
import inspect
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

from tests.framework_configs._sentinel import MISSING, STRICT_CI, _MissingSentinel
from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK

__all__ = ["AdapterConfig", "ADAPTER_CONFIGS", "ADAPTER_EXCLUDED_MODULES"]

# Populated lazily via __getattr__ to avoid top-level adapter imports.
ADAPTER_CONFIGS: list[AdapterConfig]


def _default_from_init(cls: type, param: str, fallback: Any = MISSING) -> Any:
    """Extract the default value of *param* from *cls.__init__* signature.

    Keeps test configs in sync with adapter source automatically — no need
    to hard-code model strings or other defaults here.

    Relies on ``inspect.signature(cls.__init__)``. It does not work for
    classes that use ``__init_subclass__``, custom metaclasses, or
    constructor patterns that hide defaults (e.g. attrs, Pydantic). If an
    adapter switches to such a pattern, expected_initial_values must be
    updated manually or a fallback passed.

    If the parameter is not found (e.g. the adapter accepts ``**kwargs``
    and forwards it to an underlying client), *fallback* is returned when
    provided.  Without a fallback, ``ValueError`` is raised.
    """
    sig = inspect.signature(cls.__init__)
    p = sig.parameters.get(param)
    if p is None or p.default is inspect.Parameter.empty:
        if not isinstance(fallback, _MissingSentinel):
            return fallback
        raise ValueError(
            f"{cls.__name__}.__init__ has no default for {param!r}. "
            f"If the adapter uses **kwargs, pass an explicit fallback value."
        )
    return p.default


@dataclass(frozen=True)
class AdapterConfig:
    """Describes a framework adapter for parameterized testing."""

    # Identity
    framework_id: str
    display_name: str

    # Factory: (**kwargs) -> adapter instance (handles mocking internally)
    adapter_factory: Callable[..., Any]

    # {attr_name: expected_value} verified by test_default_initialization.
    # For most adapters these are true defaults from __init__; for PydanticAI
    # ``model`` is a required kwarg injected by the factory (not a real default).
    expected_initial_values: dict[str, Any] = field(default_factory=dict)

    # For test_custom_initialization
    custom_kwargs: dict[str, Any] = field(default_factory=dict)
    custom_expected: dict[str, Any] = field(default_factory=dict)

    # Custom tools support
    has_custom_tools_attr: bool = True
    custom_tools_attr: str = "_custom_tools"

    # History converter presence
    has_history_converter: bool = True

    # Skip on_started conformance test when adapter needs live client (e.g. PydanticAI)
    skip_on_started_conformance: bool = False


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def _anthropic_factory(**kw: Any) -> Any:
    from thenvoi.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(**kw)


def _langgraph_factory(**kw: Any) -> Any:
    from thenvoi.adapters.langgraph import LangGraphAdapter

    if "llm" not in kw and "graph_factory" not in kw and "graph" not in kw:
        kw["llm"] = MagicMock()
        kw["checkpointer"] = MagicMock()
    return LangGraphAdapter(**kw)


# CrewAI conformance instances are ONLY safe for inspecting primitive
# attributes (model, role, etc.) and calling on_cleanup (which only does
# dict.pop + logging, no CrewAI interaction).  Runtime methods that interact
# with CrewAI objects (on_message, _invoke_crew) are guarded because the
# class is imported with mocked crewai dependencies.
# For runtime tests, use monkeypatch fixtures in tests/adapters/test_crewai_adapter.py.


class _MockBaseTool:
    """Minimal stand-in for ``crewai.tools.BaseTool`` at import time."""

    name: str = ""
    description: str = ""

    def __init__(self):
        pass


_crewai_import_lock = threading.Lock()


_CREWAI_AFFECTED_MODULES = (
    "thenvoi.adapters.crewai",
    "thenvoi.integrations.crewai",
    "thenvoi.integrations.crewai.runtime",
    "thenvoi.integrations.crewai.tools",
    "crewai",
    "crewai.tools",
    "nest_asyncio",
)


@functools.lru_cache(maxsize=1)
def _get_crewai_adapter_cls() -> type:
    """Import CrewAIAdapter with mocked crewai dependencies.

    Uses ``patch.dict(sys.modules, ...)`` to temporarily inject mock modules,
    imports the adapter, then cleans up.  The returned class retains mock
    references in its module globals — safe for attribute inspection only.

    Cached so the heavy sys.modules teardown/reimport happens at most once.
    Protected by ``_crewai_import_lock`` so concurrent pytest-xdist workers
    cannot interleave the ``sys.modules`` snapshot / restore sequence.

    **Save/restore semantics**: any modules that were already in
    ``sys.modules`` before this function runs are restored afterwards,
    preventing ordering-dependent failures when framework-specific tests
    (which do their own mocking) import the real adapter module first.
    """
    import importlib
    import sys
    from unittest.mock import patch

    adapter_module_name = "thenvoi.adapters.crewai"

    with _crewai_import_lock:
        # Snapshot modules that existed before we touch sys.modules.
        saved: dict[str, Any] = {}
        _sentinel = object()
        for mod in _CREWAI_AFFECTED_MODULES:
            prev = sys.modules.get(mod, _sentinel)
            if prev is not _sentinel:
                saved[mod] = prev
            sys.modules.pop(mod, None)

        mock_crewai = MagicMock()
        mock_crewai.Agent = MagicMock()
        mock_crewai.LLM = MagicMock()
        mock_crewai_tools = MagicMock()
        mock_crewai_tools.BaseTool = _MockBaseTool

        mock_entries = {
            "crewai": mock_crewai,
            "crewai.tools": mock_crewai_tools,
            "nest_asyncio": MagicMock(),
        }

        with patch.dict(sys.modules, mock_entries):
            cls = importlib.import_module(adapter_module_name).CrewAIAdapter

        # Restore pre-existing modules so framework-specific tests that
        # already imported the real adapter/crewai are not disrupted.
        # Modules that were absent before are removed (clean state).
        for mod in _CREWAI_AFFECTED_MODULES:
            if mod in saved:
                sys.modules[mod] = saved[mod]
            else:
                sys.modules.pop(mod, None)

    # Mark the class as conformance-only so accidental runtime use is detectable.
    cls._CONFORMANCE_ONLY = True
    return cls


async def _crewai_conformance_guard(*_args: Any, **_kw: Any) -> None:
    raise RuntimeError(
        "CrewAI conformance instance has mocked dependencies — "
        "use tests/adapters/test_crewai_adapter.py fixtures for runtime tests."
    )


def _crewai_factory(**kw: Any) -> Any:
    cls = _get_crewai_adapter_cls()
    assert getattr(cls, "_CONFORMANCE_ONLY", False), (
        "CrewAI adapter used here is for conformance config only; "
        "use tests/adapters/test_crewai_adapter.py fixtures for runtime tests."
    )
    instance = cls(**kw)
    # Guard runtime methods that would silently operate on MagicMock objects.
    # on_cleanup is intentionally NOT guarded — it only does dict.pop + logging
    # and does not interact with CrewAI objects.
    for method_name in ("on_message", "_invoke_crew"):
        if hasattr(instance, method_name):
            setattr(instance, method_name, _crewai_conformance_guard)
    return instance


def _claude_sdk_factory(**kw: Any) -> Any:
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(**kw)


def _pydantic_ai_factory(**kw: Any) -> Any:
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

    if "model" not in kw:
        kw["model"] = _PYDANTIC_AI_INJECTED_MODEL
    return PydanticAIAdapter(**kw)


def _parlant_factory(**kw: Any) -> Any:
    from thenvoi.adapters.parlant import ParlantAdapter

    if "server" not in kw:
        kw["server"] = MagicMock()
    if "parlant_agent" not in kw:
        mock_agent = MagicMock()
        mock_agent.id = "parlant-agent-123"
        mock_agent.name = "TestBot"
        mock_agent.create_guideline = AsyncMock(
            return_value=MagicMock(id="guideline-123")
        )
        kw["parlant_agent"] = mock_agent
    return ParlantAdapter(**kw)


def _codex_factory(**kw: Any) -> Any:
    from thenvoi.adapters.codex import CodexAdapter

    return CodexAdapter(**kw)


def _letta_factory(**kw: Any) -> Any:
    from thenvoi.adapters.letta import LettaAdapter

    return LettaAdapter(**kw)


def _opencode_factory(**kw: Any) -> Any:
    from thenvoi.adapters.opencode import OpencodeAdapter

    return OpencodeAdapter(**kw)


def _gemini_factory(**kw: Any) -> Any:
    from thenvoi.adapters.gemini import GeminiAdapter

    return GeminiAdapter(**kw)


def _google_adk_factory(**kw: Any) -> Any:
    from thenvoi.adapters.google_adk import GoogleADKAdapter

    return GoogleADKAdapter(**kw)


# ---------------------------------------------------------------------------
# Registry  (built lazily to avoid top-level adapter imports)
# ---------------------------------------------------------------------------

# PydanticAI requires ``model`` as a mandatory kwarg (no default in __init__).
# The conformance factory injects this value so the adapter can be instantiated
# without a real API key.  ``expected_initial_values["model"]`` then verifies
# the factory injection, NOT a real adapter default.
_PYDANTIC_AI_INJECTED_MODEL = "openai:gpt-5.4"


def _build_anthropic_config() -> AdapterConfig:
    from thenvoi.adapters.anthropic import AnthropicAdapter

    return AdapterConfig(
        framework_id="anthropic",
        display_name="Anthropic",
        adapter_factory=_anthropic_factory,
        expected_initial_values={
            "model": _default_from_init(AnthropicAdapter, "model"),
            "max_tokens": _default_from_init(AnthropicAdapter, "max_tokens"),
        },
        custom_kwargs={
            "model": "claude-opus-4-20250514",
            "max_tokens": 8192,
            "prompt": "Be helpful.",
        },
        custom_expected={
            "model": "claude-opus-4-20250514",
            "max_tokens": 8192,
            "_prompt": "Be helpful.",
        },
    )


def _build_langgraph_config() -> AdapterConfig:
    from thenvoi.adapters.langgraph import LangGraphAdapter

    return AdapterConfig(
        framework_id="langgraph",
        display_name="LangGraph",
        adapter_factory=_langgraph_factory,
        expected_initial_values={
            "prompt_template": _default_from_init(LangGraphAdapter, "prompt_template"),
            "custom_section": _default_from_init(LangGraphAdapter, "custom_section"),
        },
        custom_kwargs={
            "custom_section": "Be helpful.",
        },
        custom_expected={
            "custom_section": "Be helpful.",
        },
        has_custom_tools_attr=True,
        custom_tools_attr="additional_tools",
        has_history_converter=True,
    )


def _build_crewai_config() -> AdapterConfig:
    crewai_cls = _get_crewai_adapter_cls()

    try:
        import crewai  # noqa: F401

        _crewai_available = True
    except ImportError:
        _crewai_available = False

    return AdapterConfig(
        framework_id="crewai",
        display_name="CrewAI",
        adapter_factory=_crewai_factory,
        expected_initial_values={
            "model": _default_from_init(crewai_cls, "model"),
            "role": _default_from_init(crewai_cls, "role"),
            "goal": _default_from_init(crewai_cls, "goal"),
            "backstory": _default_from_init(crewai_cls, "backstory"),
            "verbose": _default_from_init(crewai_cls, "verbose"),
            "max_iter": _default_from_init(crewai_cls, "max_iter"),
            "allow_delegation": _default_from_init(crewai_cls, "allow_delegation"),
        },
        custom_kwargs={
            "model": "gpt-5.4-mini",
            "role": "Research Analyst",
            "goal": "Find and analyze information",
            "backstory": "Expert researcher",
            "custom_section": "Be thorough.",
            "verbose": True,
            "max_iter": 30,
            "max_rpm": 10,
            "allow_delegation": True,
        },
        custom_expected={
            "model": "gpt-5.4-mini",
            "role": "Research Analyst",
            "goal": "Find and analyze information",
            "backstory": "Expert researcher",
            "custom_section": "Be thorough.",
            "verbose": True,
            "max_iter": 30,
            "max_rpm": 10,
            "allow_delegation": True,
        },
        # on_started does a runtime `from crewai import Agent, LLM` which fails
        # when crewai is not installed (conflict group with parlant/pydantic-ai).
        skip_on_started_conformance=not _crewai_available,
    )


@functools.lru_cache(maxsize=1)
def _get_crewai_flow_adapter_cls() -> type:
    """Import CrewAIFlowAdapter with mocked crewai dependencies.

    The flow adapter has its own optional crewai.flow.flow import. The
    integration tools module (used by build_thenvoi_crewai_tools when a
    sub-Crew is constructed at runtime) imports crewai.tools, so the same
    mock pattern as ``_get_crewai_adapter_cls`` is reused.
    """
    import importlib
    import sys
    from unittest.mock import patch

    adapter_module_name = "thenvoi.adapters.crewai_flow"
    affected = _CREWAI_AFFECTED_MODULES + (
        adapter_module_name,
        "thenvoi.integrations.crewai",
        "thenvoi.integrations.crewai.runtime",
        "thenvoi.integrations.crewai.tools",
    )

    with _crewai_import_lock:
        saved: dict[str, Any] = {}
        _sentinel = object()
        for mod in affected:
            prev = sys.modules.get(mod, _sentinel)
            if prev is not _sentinel:
                saved[mod] = prev
            sys.modules.pop(mod, None)

        mock_crewai = MagicMock()
        mock_crewai.Agent = MagicMock()
        mock_crewai.LLM = MagicMock()
        mock_crewai_tools = MagicMock()
        mock_crewai_tools.BaseTool = _MockBaseTool
        mock_flow_module = MagicMock()
        mock_flow_module.Flow = type("Flow", (), {})

        mock_entries = {
            "crewai": mock_crewai,
            "crewai.tools": mock_crewai_tools,
            "crewai.flow": MagicMock(flow=mock_flow_module),
            "crewai.flow.flow": mock_flow_module,
            "nest_asyncio": MagicMock(),
        }

        with patch.dict(sys.modules, mock_entries):
            cls = importlib.import_module(adapter_module_name).CrewAIFlowAdapter

        for mod in affected:
            if mod in saved:
                sys.modules[mod] = saved[mod]
            else:
                sys.modules.pop(mod, None)

    cls._CONFORMANCE_ONLY = True
    return cls


def _crewai_flow_factory(**kw: Any) -> Any:
    cls = _get_crewai_flow_adapter_cls()
    if "flow_factory" not in kw:
        kw["flow_factory"] = lambda: MagicMock()
    instance = cls(**kw)

    async def _guard(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(
            "CrewAIFlow conformance instance has mocked dependencies — "
            "use tests/adapters/test_crewai_flow_*.py fixtures for runtime tests."
        )

    instance.on_message = _guard  # type: ignore[method-assign]
    return instance


def _build_crewai_flow_config() -> AdapterConfig:
    flow_cls = _get_crewai_flow_adapter_cls()

    return AdapterConfig(
        framework_id="crewai_flow",
        display_name="CrewAIFlow",
        adapter_factory=_crewai_flow_factory,
        expected_initial_values={
            "_max_delegation_rounds": _default_from_init(
                flow_cls, "max_delegation_rounds"
            ),
        },
        custom_kwargs={
            "max_delegation_rounds": 6,
        },
        custom_expected={
            "_max_delegation_rounds": 6,
        },
        has_custom_tools_attr=False,
    )


def _build_claude_sdk_config() -> AdapterConfig | None:
    from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE, ClaudeSDKAdapter

    if not _CLAUDE_SDK_AVAILABLE:
        return None  # optional dep not installed; skip in CI

    return AdapterConfig(
        framework_id="claude_sdk",
        display_name="ClaudeSDK",
        adapter_factory=_claude_sdk_factory,
        expected_initial_values={
            "model": _default_from_init(ClaudeSDKAdapter, "model"),
            "fallback_model": _default_from_init(ClaudeSDKAdapter, "fallback_model"),
            "custom_section": _default_from_init(ClaudeSDKAdapter, "custom_section"),
            "max_thinking_tokens": _default_from_init(
                ClaudeSDKAdapter, "max_thinking_tokens"
            ),
            "permission_mode": _default_from_init(ClaudeSDKAdapter, "permission_mode"),
        },
        custom_kwargs={
            "model": "claude-opus-4-20250514",
            "fallback_model": "sonnet",
            "custom_section": "Be helpful.",
            "max_thinking_tokens": 10000,
            "permission_mode": "bypassPermissions",
        },
        custom_expected={
            "model": "claude-opus-4-20250514",
            "fallback_model": "sonnet",
            "custom_section": "Be helpful.",
            "max_thinking_tokens": 10000,
            "permission_mode": "bypassPermissions",
        },
        skip_on_started_conformance=True,  # on_started creates real MCP server + ClaudeSessionManager; tested in test_claude_sdk_adapter
    )


def _build_pydantic_ai_config() -> AdapterConfig:
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

    return AdapterConfig(
        framework_id="pydantic_ai",
        display_name="PydanticAI",
        adapter_factory=_pydantic_ai_factory,
        expected_initial_values={
            # Injected by _pydantic_ai_factory, not a real __init__ default.
            # Verifies that the factory injection is stored correctly.
            "model": _PYDANTIC_AI_INJECTED_MODEL,
            "system_prompt": _default_from_init(PydanticAIAdapter, "system_prompt"),
            "custom_section": _default_from_init(PydanticAIAdapter, "custom_section"),
        },
        custom_kwargs={
            "model": "anthropic:claude-sonnet-4-5-20250929",
            "system_prompt": "You are a helpful bot.",
            "custom_section": "Be concise.",
        },
        custom_expected={
            "model": "anthropic:claude-sonnet-4-5-20250929",
            "system_prompt": "You are a helpful bot.",
            "custom_section": "Be concise.",
        },
        skip_on_started_conformance=True,  # on_started creates real OpenAI client; tested in test_pydantic_ai_adapter
    )


def _build_parlant_config() -> AdapterConfig:
    from thenvoi.adapters.parlant import ParlantAdapter

    try:
        import parlant.sdk  # noqa: F401

        _parlant_available = True
    except ImportError:
        _parlant_available = False

    return AdapterConfig(
        framework_id="parlant",
        display_name="Parlant",
        adapter_factory=_parlant_factory,
        expected_initial_values={
            "system_prompt": _default_from_init(ParlantAdapter, "system_prompt"),
            "custom_section": _default_from_init(ParlantAdapter, "custom_section"),
        },
        custom_kwargs={
            "system_prompt": "Custom system prompt",
            "custom_section": "Be helpful.",
        },
        custom_expected={
            "system_prompt": "Custom system prompt",
            "custom_section": "Be helpful.",
        },
        has_custom_tools_attr=True,
        custom_tools_attr="_custom_tools",
        # on_started does a runtime `from parlant.core.application import Application`
        # which fails when parlant SDK is not installed (conflict group with crewai).
        skip_on_started_conformance=not _parlant_available,
    )


def _build_codex_config() -> AdapterConfig:
    from thenvoi.adapters.codex import CodexAdapterConfig

    return AdapterConfig(
        framework_id="codex",
        display_name="Codex",
        adapter_factory=_codex_factory,
        expected_initial_values={
            "_custom_tools": [],
            "config": CodexAdapterConfig(),
        },
        custom_kwargs={
            "config": CodexAdapterConfig(enable_execution_reporting=True),
        },
        custom_expected={
            "config": CodexAdapterConfig(enable_execution_reporting=True),
        },
        has_custom_tools_attr=True,
        custom_tools_attr="_custom_tools",
        skip_on_started_conformance=True,  # on_started creates live Codex client
    )


def _build_letta_config() -> AdapterConfig:
    from thenvoi.adapters.letta import LettaAdapterConfig

    return AdapterConfig(
        framework_id="letta",
        display_name="Letta",
        adapter_factory=_letta_factory,
        expected_initial_values={
            "config": LettaAdapterConfig(),
        },
        custom_kwargs={
            "config": LettaAdapterConfig(
                enable_execution_reporting=True,
                mode="shared",
                mcp_server_url="http://mcp:9000/sse",
            ),
        },
        custom_expected={
            "config": LettaAdapterConfig(
                enable_execution_reporting=True,
                mode="shared",
                mcp_server_url="http://mcp:9000/sse",
            ),
        },
        has_custom_tools_attr=False,
        skip_on_started_conformance=True,  # on_started registers MCP server + creates live Letta client
    )


def _build_opencode_config() -> AdapterConfig:
    from thenvoi.adapters.opencode import OpencodeAdapterConfig

    return AdapterConfig(
        framework_id="opencode",
        display_name="OpenCode",
        adapter_factory=_opencode_factory,
        expected_initial_values={
            "_custom_tools": [],
            "config": OpencodeAdapterConfig(),
        },
        custom_kwargs={
            "config": OpencodeAdapterConfig(
                enable_execution_reporting=True,
                approval_mode="auto_accept",
                provider_id="opencode",
                model_id="minimax-m2.5-free",
            ),
        },
        custom_expected={
            "config": OpencodeAdapterConfig(
                enable_execution_reporting=True,
                approval_mode="auto_accept",
                provider_id="opencode",
                model_id="minimax-m2.5-free",
            ),
        },
        has_custom_tools_attr=True,
        custom_tools_attr="_custom_tools",
    )


def _build_gemini_config() -> AdapterConfig:
    from thenvoi.adapters.gemini import GeminiAdapter

    return AdapterConfig(
        framework_id="gemini",
        display_name="Gemini",
        adapter_factory=_gemini_factory,
        expected_initial_values={
            "model": _default_from_init(GeminiAdapter, "model"),
            "system_prompt": _default_from_init(GeminiAdapter, "system_prompt"),
        },
        custom_kwargs={
            "model": "gemini-2.5-flash",
            "system_prompt": "You are a helpful bot.",
            "prompt": "Be concise.",
        },
        custom_expected={
            "model": "gemini-2.5-flash",
            "system_prompt": "You are a helpful bot.",
            "_prompt": "Be concise.",
        },
    )


# Adapter modules intentionally excluded from conformance tests.
# a2a / a2a_gateway use the A2A protocol (Google Agent-to-Agent) which has a
# fundamentally different lifecycle than framework adapters (no on_message /
# on_cleanup contract), so they cannot share the same conformance tests.
# acp uses the ACP protocol (Agent Client Protocol) with a similar non-standard
# lifecycle (ACP JSON-RPC over stdio), so it is also excluded.
# claude_sdk is excluded when claude-agent-sdk optional dep is not installed.

_excluded = {"a2a", "a2a_gateway", "acp"}
if not _HAS_CLAUDE_SDK:
    _excluded = _excluded | {"claude_sdk"}
ADAPTER_EXCLUDED_MODULES: frozenset[str] = frozenset(_excluded)


def _build_google_adk_config() -> AdapterConfig:
    from thenvoi.adapters.google_adk import GoogleADKAdapter

    return AdapterConfig(
        framework_id="google_adk",
        display_name="GoogleADK",
        adapter_factory=_google_adk_factory,
        expected_initial_values={
            "model": _default_from_init(GoogleADKAdapter, "model"),
            "custom_section": _default_from_init(GoogleADKAdapter, "custom_section"),
            "max_history_messages": _default_from_init(
                GoogleADKAdapter, "max_history_messages"
            ),
            "max_transcript_chars": _default_from_init(
                GoogleADKAdapter, "max_transcript_chars"
            ),
        },
        custom_kwargs={
            "model": "gemini-2.5-pro",
            "custom_section": "Be helpful.",
        },
        custom_expected={
            "model": "gemini-2.5-pro",
            "custom_section": "Be helpful.",
        },
        skip_on_started_conformance=False,
    )


_ADAPTER_CONFIG_BUILDERS: list[Callable[[], AdapterConfig]] = [
    _build_anthropic_config,
    _build_langgraph_config,
    _build_crewai_config,
    _build_crewai_flow_config,
    _build_claude_sdk_config,
    _build_pydantic_ai_config,
    _build_parlant_config,
    _build_codex_config,
    _build_letta_config,
    _build_opencode_config,
    _build_gemini_config,
    _build_google_adk_config,
]


@functools.lru_cache(maxsize=1)
def _build_adapter_configs() -> list[AdapterConfig]:
    """Build configs lazily so adapter imports happen only when needed.

    Each framework config is built independently so that an import failure
    in one framework does not prevent the remaining frameworks from being
    tested.  In CI, failures are raised immediately to surface broken configs.
    """
    import logging

    logger = logging.getLogger(__name__)
    configs: list[AdapterConfig] = []
    for builder in _ADAPTER_CONFIG_BUILDERS:
        try:
            result = builder()
            if result is not None:
                configs.append(result)
        except Exception as exc:
            if STRICT_CI:
                raise RuntimeError(
                    f"Adapter config builder {builder.__name__} failed in CI: {exc}"
                ) from exc
            logger.warning("Skipping adapter config from %s: %s", builder.__name__, exc)
    return configs


def __getattr__(name: str) -> Any:
    if name == "ADAPTER_CONFIGS":
        return _build_adapter_configs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
