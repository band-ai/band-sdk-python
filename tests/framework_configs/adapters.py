"""Adapter configuration registry for parameterized conformance tests.

Each AdapterConfig describes a framework adapter's properties, default values,
custom initialization kwargs, and factory function so that conformance tests can
run identical logic across all registered adapters.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import MagicMock

from tests.framework_configs.sentinel import MISSING, STRICT_CI, MissingSentinel
from band.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE as _HAS_COPILOT_SDK

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
        if not isinstance(fallback, MissingSentinel):
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
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(**kw)


def _langgraph_factory(**kw: Any) -> Any:
    from band.adapters.langgraph import LangGraphAdapter

    if "llm" not in kw and "graph_factory" not in kw and "graph" not in kw:
        kw["llm"] = MagicMock()
        kw["checkpointer"] = MagicMock()
    return LangGraphAdapter(**kw)


# The CrewAI conformance instance is config-only: safe for inspecting primitive
# attributes (model, role, etc.) and on_cleanup, never for runtime work — the
# factories below guard the methods that would build a Crew or hit an LLM.
# For runtime tests, use monkeypatch fixtures in tests/adapters/test_crewai_adapter.py.


def _crewai_installed() -> bool:
    """Whether the real crewai package is importable (the dev-crewai venv)."""
    try:
        import crewai  # noqa: F401
    except ImportError:
        return False
    return True


def _get_crewai_adapter_cls() -> type:
    """The CrewAIAdapter class for the conformance config.

    A plain import needs no crewai: every crewai import in the adapter is
    TYPE_CHECKING-only or function-local, so the module loads and the class
    constructs with the package absent. Do not fake crewai through ``sys.modules``
    to get here — see ``tests/test_module_isolation.py`` for what that costs.
    """
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter


async def _crewai_conformance_guard(*_args: Any, **_kw: Any) -> None:
    raise RuntimeError(
        "CrewAI conformance instance is config-only — "
        "use tests/adapters/test_crewai_adapter.py fixtures for runtime tests."
    )


def _crewai_factory(**kw: Any) -> Any:
    cls = _get_crewai_adapter_cls()
    instance = cls(**kw)
    # Guard the runtime methods in both venvs: without crewai they would fail on its
    # function-local import, and with it they would build a Crew and call an LLM for
    # real. on_cleanup is never guarded (dict.pop + logging, no CrewAI interaction).
    for method_name in ("on_message", "_invoke_crew"):
        if hasattr(instance, method_name):
            setattr(instance, method_name, _crewai_conformance_guard)
    return instance


def _claude_sdk_factory(**kw: Any) -> Any:
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(**kw)


def _pydantic_ai_factory(**kw: Any) -> Any:
    from band.adapters.pydantic_ai import PydanticAIAdapter

    if "model" not in kw:
        kw["model"] = _PYDANTIC_AI_INJECTED_MODEL
    return PydanticAIAdapter(**kw)


def _strands_factory(**kw: Any) -> Any:
    from band.adapters.strands import StrandsAdapter

    if "model" not in kw:
        kw["model"] = _STRANDS_INJECTED_MODEL
    return StrandsAdapter(**kw)


def _parlant_factory(**kw: Any) -> Any:
    from band.adapters.parlant import ParlantAdapter

    if "server" not in kw:
        kw["server"] = MagicMock()
    if "parlant_agent" not in kw:
        mock_agent = MagicMock()
        mock_agent.id = "parlant-agent-123"
        mock_agent.name = "TestBot"
        kw["parlant_agent"] = mock_agent
    return ParlantAdapter(**kw)


def _codex_factory(**kw: Any) -> Any:
    from band.adapters.codex import CodexAdapter

    return CodexAdapter(**kw)


def _letta_factory(**kw: Any) -> Any:
    from band.adapters.letta import LettaAdapter

    return LettaAdapter(**kw)


def _opencode_factory(**kw: Any) -> Any:
    from band.adapters.opencode import OpencodeAdapter

    # Fake the server boundary so on_started's reachability preflight
    # (which only runs with the default client factory) stays offline.
    kw.setdefault("client_factory", lambda _config: MagicMock())
    return OpencodeAdapter(**kw)


def _agno_factory(**kw: Any) -> Any:
    from band.adapters.agno import AgnoAdapter

    # AgnoAdapter takes a developer-built Agno Agent; inject a stand-in so the
    # adapter can be constructed without a real model/API key.
    if "agent" not in kw:
        kw["agent"] = MagicMock()
    return AgnoAdapter(**kw)


def _gemini_factory(**kw: Any) -> Any:
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(**kw)


def _google_adk_factory(**kw: Any) -> Any:
    from band.adapters.google_adk import GoogleADKAdapter

    return GoogleADKAdapter(**kw)


# ---------------------------------------------------------------------------
# Registry  (built lazily to avoid top-level adapter imports)
# ---------------------------------------------------------------------------

# PydanticAI requires ``model`` as a mandatory kwarg (no default in __init__).
# The conformance factory injects this value so the adapter can be instantiated
# without a real API key.  ``expected_initial_values["model"]`` then verifies
# the factory injection, NOT a real adapter default.
_PYDANTIC_AI_INJECTED_MODEL = "openai:gpt-5.4"

# Strands likewise requires ``model``. A plain string is fine at construction
# time: the adapter passes it through to Strands (which treats strings as
# Bedrock model ids), and no client is created until the first message turn.
_STRANDS_INJECTED_MODEL = "strands-conformance-model"


def _build_anthropic_config() -> AdapterConfig:
    from band.adapters.anthropic import AnthropicAdapter

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
    from band.adapters.langgraph import LangGraphAdapter

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
    _crewai_available = _crewai_installed()

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


def _get_crewai_flow_adapter_cls() -> type:
    """The CrewAIFlowAdapter class for the conformance config.

    Plain import, as for ``_get_crewai_adapter_cls``. The adapter no longer
    imports ``Flow`` at module scope, so this remains safe when crewai is absent.
    """
    from band.adapters.crewai_flow import CrewAIFlowAdapter

    return CrewAIFlowAdapter


def _crewai_flow_factory(**kw: Any) -> Any:
    cls = _get_crewai_flow_adapter_cls()
    if "flow_factory" not in kw:
        kw["flow_factory"] = lambda: MagicMock()
    instance = cls(**kw)

    async def _guard(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(
            "CrewAIFlow conformance instance is config-only — "
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


def _copilot_sdk_factory(**kw: Any) -> Any:
    from band.adapters.copilot_sdk import CopilotSDKAdapter

    return CopilotSDKAdapter(**kw)


def _build_copilot_sdk_config() -> AdapterConfig | None:
    from band.adapters.copilot_sdk import (
        _COPILOT_SDK_AVAILABLE,
        CopilotSDKAdapterConfig,
    )

    if not _COPILOT_SDK_AVAILABLE:
        return None  # optional dep not installed; skip in CI

    custom = CopilotSDKAdapterConfig(
        model="gpt-5",
        custom_section="Be helpful.",
        reasoning_effort="high",
        session_id_prefix="custom-",
        turn_timeout_s=45.0,
    )
    return AdapterConfig(
        framework_id="copilot_sdk",
        display_name="CopilotSDK",
        adapter_factory=_copilot_sdk_factory,
        expected_initial_values={
            "_custom_tools": [],
            "config": CopilotSDKAdapterConfig(),
        },
        custom_kwargs={"config": custom},
        custom_expected={"config": custom},
        skip_on_started_conformance=True,  # on_started creates a real CopilotClient; tested in test_copilot_sdk_adapter
    )


def _build_claude_sdk_config() -> AdapterConfig | None:
    from band.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE, ClaudeSDKAdapter

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
    from band.adapters.pydantic_ai import PydanticAIAdapter

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


def _build_strands_config() -> AdapterConfig:
    from band.adapters.strands import StrandsAdapter

    return AdapterConfig(
        framework_id="strands",
        display_name="Strands",
        adapter_factory=_strands_factory,
        expected_initial_values={
            # Injected by _strands_factory, not a real __init__ default.
            # Verifies that the factory injection is stored correctly.
            "model": _STRANDS_INJECTED_MODEL,
            "system_prompt": _default_from_init(StrandsAdapter, "system_prompt"),
            "custom_section": _default_from_init(StrandsAdapter, "custom_section"),
        },
        custom_kwargs={
            "model": "custom-bedrock-model-id",
            "system_prompt": "You are a helpful bot.",
            "custom_section": "Be concise.",
        },
        custom_expected={
            "model": "custom-bedrock-model-id",
            "system_prompt": "You are a helpful bot.",
            "custom_section": "Be concise.",
        },
    )


def _build_parlant_config() -> AdapterConfig:
    from band.adapters.parlant import ParlantAdapter

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
        has_custom_tools_attr=False,
        # on_started does a runtime `from parlant.core.application import Application`
        # which fails when parlant SDK is not installed (conflict group with crewai).
        skip_on_started_conformance=not _parlant_available,
    )


def _build_codex_config() -> AdapterConfig:
    from band.adapters.codex import CodexAdapterConfig

    return AdapterConfig(
        framework_id="codex",
        display_name="Codex",
        adapter_factory=_codex_factory,
        expected_initial_values={
            "_custom_tools": [],
            "config": CodexAdapterConfig(),
        },
        custom_kwargs={
            "config": CodexAdapterConfig(structured_errors=False),
        },
        custom_expected={
            "config": CodexAdapterConfig(structured_errors=False),
        },
        has_custom_tools_attr=True,
        custom_tools_attr="_custom_tools",
        skip_on_started_conformance=True,  # on_started creates live Codex client
    )


def _build_letta_config() -> AdapterConfig:
    from band.adapters.letta import LettaAdapterConfig, LettaMCPConfig

    return AdapterConfig(
        framework_id="letta",
        display_name="Letta",
        adapter_factory=_letta_factory,
        expected_initial_values={
            "config": LettaAdapterConfig(),
        },
        custom_kwargs={
            "config": LettaAdapterConfig(
                auto_relay=False,
                mode="shared",
                mcp=LettaMCPConfig(mode="external", server_url="http://mcp:9000/sse"),
            ),
        },
        custom_expected={
            "config": LettaAdapterConfig(
                auto_relay=False,
                mode="shared",
                mcp=LettaMCPConfig(mode="external", server_url="http://mcp:9000/sse"),
            ),
        },
        has_custom_tools_attr=False,
        skip_on_started_conformance=True,  # on_started registers MCP server + creates live Letta client
    )


def _build_opencode_config() -> AdapterConfig:
    from band.adapters.opencode import OpencodeAdapterConfig

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
                include_base_instructions=True,
                approval_mode="auto_accept",
                provider_id="opencode",
                model_id="minimax-m2.5-free",
            ),
        },
        custom_expected={
            "config": OpencodeAdapterConfig(
                include_base_instructions=True,
                approval_mode="auto_accept",
                provider_id="opencode",
                model_id="minimax-m2.5-free",
            ),
        },
        has_custom_tools_attr=True,
        custom_tools_attr="_custom_tools",
    )


def _build_agno_config() -> AdapterConfig:
    return AdapterConfig(
        framework_id="agno",
        display_name="Agno",
        adapter_factory=_agno_factory,
        # AgnoAdapter has no model/prompt of its own (the caller's Agno agent
        # owns those); assert the adapter-level state instead.
        expected_initial_values={
            "agent": None,  # the run copy is built in on_started
            # Band tools are resolved per-run via a callable factory installed in
            # on_started, cached by contact-flag; nothing is cached before start.
            "_band_tools_cache": {},
        },
        # No model/prompt kwargs to customize; nothing to assert here.
        custom_kwargs={},
        custom_expected={},
        # AgnoAdapter does not expose Band custom tools (no additional_tools).
        has_custom_tools_attr=False,
    )


def _build_gemini_config() -> AdapterConfig:
    from band.adapters.gemini import GeminiAdapter

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
# copilot_acp is a thin ACPClientAdapter subclass (Copilot CLI over ACP): it shares
# the excluded acp bridge's lifecycle and converter and adds no model/LLM contract
# of its own, so it is excluded for the same reason as acp. It is exercised live via
# the baseline matrix (backends lane), not the framework-conformance matrix.
# slack is a transport bridge that *wraps* an inner framework adapter (the brain)
# and adds Slack ingress/egress; it has no model/LLM contract of its own, so it
# cannot share the framework-adapter conformance tests (same rationale as a2a/acp).
# claude_sdk is excluded when claude-agent-sdk optional dep is not installed.
# copilot_sdk is excluded when github-copilot-sdk optional dep is not installed.

_excluded = {"a2a", "a2a_gateway", "acp", "copilot_acp", "slack"}
if not _HAS_CLAUDE_SDK:
    _excluded = _excluded | {"claude_sdk"}
if not _HAS_COPILOT_SDK:
    _excluded = _excluded | {"copilot_sdk"}
ADAPTER_EXCLUDED_MODULES: frozenset[str] = frozenset(_excluded)


def _build_google_adk_config() -> AdapterConfig:
    from band.adapters.google_adk import GoogleADKAdapter

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
    _build_copilot_sdk_config,
    _build_pydantic_ai_config,
    _build_strands_config,
    _build_parlant_config,
    _build_codex_config,
    _build_letta_config,
    _build_opencode_config,
    _build_agno_config,
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
