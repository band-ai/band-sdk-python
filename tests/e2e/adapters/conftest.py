"""Adapter factory fixtures for E2E tests.

Provides factory functions that create real adapter instances configured
with cheap LLM models for E2E testing. Each factory returns an adapter
ready to be used with Agent.create().

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/ -v -s --no-cov
"""

from __future__ import annotations

import importlib.util
import logging
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Emit

from tests.e2e.baseline_artifacts import provider_usage_blocked_reason
from tests.e2e.conftest import E2ESettings
from tests.e2e.settings_groups import CodexSettings, LettaSettings, OpencodeSettings

logger = logging.getLogger(__name__)

# Type alias for adapter factory functions
AdapterFactory = Callable[[E2ESettings], SimpleAdapter[Any]]


# Self-registering factory registry: a factory declares its adapter id + the
# group(s) it belongs to via @adapter_factory, so the public group dicts below
# are assembled from the decorations instead of hand-maintained lists. A new
# adapter just decorates its factory; the drift gate in test_e2e_hygiene.py
# fails closed if a non-bridge adapter ends up in no group and no exclusion.
_FACTORY_REGISTRY: dict[str, dict[str, AdapterFactory]] = {}


def adapter_factory(
    adapter_id: str, *, groups: tuple[str, ...]
) -> Callable[[AdapterFactory], AdapterFactory]:
    """Register a factory under *adapter_id* for each of *groups*."""

    def deco(fn: AdapterFactory) -> AdapterFactory:
        for group in groups:
            _FACTORY_REGISTRY.setdefault(group, {})[adapter_id] = fn
        return fn

    return deco


# =============================================================================
# Individual Adapter Factories
# =============================================================================


def _require_openai_key(settings: E2ESettings) -> None:
    """Skip test if the OpenAI API key is not set."""
    if not settings.openai.api_key:
        pytest.skip("OPENAI_API_KEY not set")


def _require_anthropic_key(settings: E2ESettings) -> None:
    """Skip test if the Anthropic API key is not set."""
    if not settings.anthropic.api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")


def _require_gemini_key_or_vertex(settings: E2ESettings) -> None:
    """Skip test if neither Gemini Developer API nor Vertex AI is configured."""
    if not settings.google.has_credentials:
        pytest.skip("GOOGLE_API_KEY/GEMINI_API_KEY or Vertex AI env not set")


def _is_conflicting_crewai_lane() -> bool:
    """Detect the default dev lane that cannot safely run CrewAI E2E tests."""
    return any(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("parlant", "pydantic_ai")
    )


def _safe_approval_mode(
    *,
    adapter_name: str,
    mode: str,
    opted_in: bool,
) -> str:
    if mode == "auto_accept" and not opted_in:
        pytest.skip(
            f"{adapter_name} E2E auto_accept requires "
            "E2E_ALLOW_WRITE_CAPABLE_AUTO_APPROVAL=true"
        )
    return mode


def _require_codex_disposable_cwd(codex: CodexSettings) -> str:
    if not codex.cwd:
        pytest.skip("CODEX_CWD must point to an explicit disposable directory")
    path = Path(codex.cwd).expanduser().resolve()
    if not path.is_dir():
        pytest.skip(f"CODEX_CWD must be an existing directory: {path}")
    if not codex.cwd_is_disposable:
        pytest.skip("CODEX E2E requires E2E_CODEX_CWD_IS_DISPOSABLE=true")
    repo_root = Path(__file__).resolve().parents[3]
    if path == repo_root or repo_root in path.parents:
        pytest.skip("CODEX_CWD must not be inside the SDK repository")
    return str(path)


@adapter_factory("langgraph", groups=("default",))
def create_langgraph_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a LangGraph adapter with a cheap OpenAI model."""
    _require_openai_key(settings)
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section="Keep responses short and direct.",
    )


@adapter_factory("anthropic", groups=("default",))
def create_anthropic_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Anthropic adapter with a cheap Claude model."""
    _require_anthropic_key(settings)
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        prompt="Keep responses short and direct.",
    )


@adapter_factory("pydantic_ai", groups=("default",))
def create_pydantic_ai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Pydantic AI adapter with a cheap OpenAI model."""
    _require_openai_key(settings)
    from band.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(
        model=f"openai:{settings.e2e_llm_model}",
        custom_section="Keep responses short and direct.",
    )


@adapter_factory("claude_sdk", groups=("default",))
def create_claude_sdk_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Claude SDK adapter with a cheap Claude model."""
    _require_anthropic_key(settings)
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and direct.",
    )


@adapter_factory("crewai", groups=("default",))
def create_crewai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI adapter with a cheap OpenAI model."""
    _require_openai_key(settings)
    if _is_conflicting_crewai_lane():
        pytest.skip("crewai E2E requires the dev-crewai lane")
    pytest.importorskip("crewai", reason="crewai E2E requires the dev-crewai lane")
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=settings.e2e_llm_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing",
        backstory="A test agent for E2E validation.",
        custom_section="Keep responses short and concise.",
    )


@adapter_factory("crewai_flow", groups=("default",))
def create_crewai_flow_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI Flow adapter whose real side-effect path sends a reply."""
    from band.adapters.crewai_flow import CrewAIFlowAdapter

    class _E2EFlow:
        async def kickoff_async(self, inputs: dict[str, Any]) -> dict[str, Any]:
            message = inputs.get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            reply = (
                "PINEAPPLE" if "PINEAPPLE" in content else "E2E CrewAI Flow response"
            )
            return {
                "decision": "direct_response",
                "content": reply,
                "mentions": [],
            }

    return CrewAIFlowAdapter(flow_factory=_E2EFlow)


@adapter_factory("opencode", groups=("default", "baseline_l0"))
def create_opencode_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an OpenCode adapter backed by a caller-provided server."""
    opencode = OpencodeSettings()
    if not opencode.base_url:
        pytest.skip("OPENCODE_BASE_URL not set (needed for OpenCode E2E)")

    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=opencode.base_url,
            provider_id=opencode.provider_id,
            model_id=opencode.model_id,
            agent=opencode.agent or None,
            custom_section="Keep responses short and concise.",
            approval_mode=_safe_approval_mode(
                adapter_name="OpenCode",
                mode=opencode.approval_mode,
                opted_in=opencode.allow_write_capable_auto_approval,
            ),
            question_mode=opencode.question_mode,
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


@adapter_factory("codex", groups=("default", "baseline_l0"))
def create_codex_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Codex adapter backed by the local Codex CLI/app-server."""
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    codex = CodexSettings()
    if codex.transport not in {"stdio", "ws"}:
        pytest.skip("CODEX_TRANSPORT must be 'stdio' or 'ws' for Codex E2E")

    command = tuple(shlex.split(codex.command)) if codex.command else None
    binary = command[0] if command else "codex"
    if codex.transport == "stdio" and not shutil.which(binary):
        pytest.skip("Codex E2E requires the codex CLI on PATH")
    cwd = _require_codex_disposable_cwd(codex)

    return CodexAdapter(
        config=CodexAdapterConfig(
            transport=cast(Any, codex.transport),
            codex_command=command,
            codex_ws_url=codex.ws_url,
            model=codex.model or settings.e2e_llm_model,
            cwd=cwd,
            approval_policy=codex.approval_policy,
            approval_mode=cast(
                Any,
                _safe_approval_mode(
                    adapter_name="Codex",
                    mode=codex.approval_mode,
                    opted_in=codex.allow_write_capable_auto_approval,
                ),
            ),
            custom_section="Keep responses short and direct.",
            enable_task_events=False,
            enable_execution_reporting=False,
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


@adapter_factory("letta", groups=("default", "baseline_l0"))
def create_letta_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Letta adapter backed by Letta Cloud or a self-hosted server."""
    pytest.importorskip("letta_client", reason="letta-client not installed")

    letta = LettaSettings()
    base_url = letta.base_url
    provider_key = letta.api_key or None
    mcp_server_url = letta.mcp_server_url

    if base_url.rstrip("/") == "https://api.letta.com":
        if not provider_key:
            pytest.skip("LETTA_API_KEY not set (needed for Letta Cloud E2E)")
        if not mcp_server_url:
            pytest.skip("MCP_SERVER_URL not set (needed for Letta Cloud E2E)")
        mcp_host = urlparse(mcp_server_url).hostname
        if mcp_host in {"localhost", "127.0.0.1", "0.0.0.0"}:
            pytest.skip("Letta Cloud E2E needs a publicly reachable MCP_SERVER_URL")
    else:
        mcp_server_url = mcp_server_url or "http://localhost:8002/sse"

    from band.adapters.letta import LettaAdapter, LettaAdapterConfig

    return LettaAdapter(
        config=LettaAdapterConfig(
            base_url=base_url,
            provider_key=provider_key,
            project=letta.project or None,
            model=letta.model,
            mcp_server_url=mcp_server_url,
            custom_section="Keep responses short and concise.",
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


# =============================================================================
# Baseline Default Adapter Factories
# =============================================================================


@adapter_factory("langgraph", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_langgraph_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered LangGraph adapter for baseline live proof."""
    _require_openai_key(settings)
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
    )


@adapter_factory("anthropic", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_anthropic_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Anthropic adapter for baseline live proof."""
    _require_anthropic_key(settings)
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(model=settings.e2e_anthropic_model)


@adapter_factory("pydantic_ai", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_pydantic_ai_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Pydantic AI adapter for baseline live proof."""
    _require_openai_key(settings)
    from band.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(model=f"openai:{settings.e2e_llm_model}")


@adapter_factory("claude_sdk", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_claude_sdk_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Claude SDK adapter for baseline live proof."""
    _require_anthropic_key(settings)
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(model=settings.e2e_anthropic_model)


@adapter_factory("gemini", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_gemini_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an unsteered Gemini adapter for baseline live proof."""
    _require_gemini_key_or_vertex(settings)
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(model=settings.e2e_gemini_model)


@adapter_factory("google_adk", groups=("baseline_default", "baseline_l0"))
def create_baseline_default_google_adk_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Google ADK adapter for baseline live proof."""
    _require_gemini_key_or_vertex(settings)
    from band.adapters.google_adk import GoogleADKAdapter

    return GoogleADKAdapter(model=settings.e2e_gemini_model)


# =============================================================================
# Adapter Registry
# =============================================================================

# Assembled from the @adapter_factory decorations above (not hand-maintained).
# A drift gate in tests/e2e/test_e2e_hygiene.py asserts these groups + their
# declared exclusions cover every non-bridge adapter module on disk.
ADAPTER_FACTORIES: dict[str, AdapterFactory] = dict(_FACTORY_REGISTRY["default"])

BASELINE_DEFAULT_ADAPTER_FACTORIES: dict[str, AdapterFactory] = dict(
    _FACTORY_REGISTRY["baseline_default"]
)

# Non-bridge adapters intentionally absent from each group, with the reason.
# The drift gate requires group keys + these exclusions == all non-bridge
# adapters, so a newly added adapter must be classified here or registered.
DEFAULT_GROUP_EXCLUSIONS: dict[str, str] = {
    "gemini": "exercised via the baseline-default lanes, not the general smoke set",
    "google_adk": "exercised via the baseline-default lanes, not the general smoke set",
    "parlant": "requires a running Parlant server; covered by its dedicated e2e file",
}
BASELINE_DEFAULT_GROUP_EXCLUSIONS: dict[str, str] = {
    "crewai": "dev-crewai dependency lane; conflicts with parlant/pydantic-ai",
    "crewai_flow": "proves terminal-return side effects in a dedicated flow file",
    "opencode": "out-of-process server runtime; runs in the L0 lane, not baseline-default",
    "codex": "out-of-process subprocess runtime; runs in the L0 lane, not baseline-default",
    "letta": "remote server runtime; runs in the L0 lane, not baseline-default",
    "parlant": "requires a running Parlant server; covered by its dedicated e2e file",
}

PROVIDER_USAGE_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    name: factory
    for name, factory in ADAPTER_FACTORIES.items()
    if provider_usage_blocked_reason(name) is None
}
PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = tuple(
    name
    for name in ADAPTER_FACTORIES
    if provider_usage_blocked_reason(name) is not None
)
BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    name: factory
    for name, factory in BASELINE_DEFAULT_ADAPTER_FACTORIES.items()
    if provider_usage_blocked_reason(name) is None
}
_BASELINE_EXTRA_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = ("parlant",)
BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        name
        for name in [
            *ADAPTER_FACTORIES,
            *BASELINE_DEFAULT_ADAPTER_FACTORIES,
            *_BASELINE_EXTRA_BLOCKED_ADAPTER_NAMES,
        ]
        if provider_usage_blocked_reason(name) is not None
    )
)
BASELINE_L0_ADAPTER_FACTORIES: dict[str, AdapterFactory] = dict(
    _FACTORY_REGISTRY["baseline_l0"]
)
BASELINE_L0_BLOCKED_ADAPTER_NAMES: tuple[str, ...] = (
    "crewai",
    "crewai_flow",
    "parlant",
)

# Note: CrewAI Flow and Parlant are excluded from the default parametrized set.
# CrewAI Flow proves terminal-return side effects in a dedicated file, and
# Parlant requires a running server plus adapter-specific setup.


# Note: The parametrized `adapter_entry` fixture lives in tests/e2e/conftest.py
# so it is shared between adapters/ and scenarios/ tests.
