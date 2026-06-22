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
import os
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

logger = logging.getLogger(__name__)

# Type alias for adapter factory functions
AdapterFactory = Callable[[E2ESettings], SimpleAdapter[Any]]


# =============================================================================
# Individual Adapter Factories
# =============================================================================


def _require_openai_key() -> None:
    """Skip test if OPENAI_API_KEY is not set."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")


def _require_anthropic_key() -> None:
    """Skip test if ANTHROPIC_API_KEY is not set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")


def _require_gemini_key_or_vertex() -> None:
    """Skip test if neither Gemini Developer API nor Vertex AI env is configured."""
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "true" and os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    ):
        return
    pytest.skip("GOOGLE_API_KEY/GEMINI_API_KEY or Vertex AI env not set")


def _is_conflicting_crewai_lane() -> bool:
    """Detect the default dev lane that cannot safely run CrewAI E2E tests."""
    return any(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("parlant", "pydantic_ai")
    )


def _write_capable_auto_approval_opted_in() -> bool:
    return os.environ.get("E2E_ALLOW_WRITE_CAPABLE_AUTO_APPROVAL") == "true"


def _safe_approval_mode(
    *,
    adapter_name: str,
    env_var: str,
    default: str,
) -> str:
    mode = os.environ.get(env_var, default)
    if mode == "auto_accept" and not _write_capable_auto_approval_opted_in():
        pytest.skip(
            f"{adapter_name} E2E auto_accept requires "
            "E2E_ALLOW_WRITE_CAPABLE_AUTO_APPROVAL=true"
        )
    return mode


def _require_codex_disposable_cwd() -> str:
    cwd = os.environ.get("CODEX_CWD")
    if not cwd:
        pytest.skip("CODEX_CWD must point to an explicit disposable directory")
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        pytest.skip(f"CODEX_CWD must be an existing directory: {path}")
    if os.environ.get("E2E_CODEX_CWD_IS_DISPOSABLE") != "true":
        pytest.skip("CODEX E2E requires E2E_CODEX_CWD_IS_DISPOSABLE=true")
    repo_root = Path(__file__).resolve().parents[3]
    if path == repo_root or repo_root in path.parents:
        pytest.skip("CODEX_CWD must not be inside the SDK repository")
    return str(path)


def create_langgraph_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a LangGraph adapter with a cheap OpenAI model."""
    _require_openai_key()
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section="Keep responses short and direct.",
    )


def create_anthropic_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Anthropic adapter with a cheap Claude model."""
    _require_anthropic_key()
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        prompt="Keep responses short and direct.",
    )


def create_pydantic_ai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Pydantic AI adapter with a cheap OpenAI model."""
    _require_openai_key()
    from band.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(
        model=f"openai:{settings.e2e_llm_model}",
        custom_section="Keep responses short and direct.",
    )


def create_claude_sdk_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Claude SDK adapter with a cheap Claude model."""
    _require_anthropic_key()
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and direct.",
    )


def create_crewai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI adapter with a cheap OpenAI model."""
    _require_openai_key()
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


def create_opencode_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an OpenCode adapter backed by a caller-provided server."""
    base_url = os.environ.get("OPENCODE_BASE_URL")
    if not base_url:
        pytest.skip("OPENCODE_BASE_URL not set (needed for OpenCode E2E)")

    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=base_url,
            provider_id=os.environ.get("OPENCODE_PROVIDER_ID", "opencode"),
            model_id=os.environ.get("OPENCODE_MODEL_ID", "minimax-m2.5-free"),
            agent=os.environ.get("OPENCODE_AGENT") or None,
            custom_section="Keep responses short and concise.",
            approval_mode=_safe_approval_mode(
                adapter_name="OpenCode",
                env_var="OPENCODE_APPROVAL_MODE",
                default="auto_decline",
            ),
            question_mode=os.environ.get("OPENCODE_QUESTION_MODE", "auto_reject"),
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def create_codex_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Codex adapter backed by the local Codex CLI/app-server."""
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    transport = os.environ.get("CODEX_TRANSPORT", "stdio")
    if transport not in {"stdio", "ws"}:
        pytest.skip("CODEX_TRANSPORT must be 'stdio' or 'ws' for Codex E2E")

    command_text = os.environ.get("CODEX_COMMAND")
    command = tuple(shlex.split(command_text)) if command_text else None
    binary = command[0] if command else "codex"
    if transport == "stdio" and not shutil.which(binary):
        pytest.skip("Codex E2E requires the codex CLI on PATH")
    cwd = _require_codex_disposable_cwd()

    return CodexAdapter(
        config=CodexAdapterConfig(
            transport=cast(Any, transport),
            codex_command=command,
            codex_ws_url=os.environ.get("CODEX_WS_URL", "ws://127.0.0.1:8765"),
            model=os.environ.get("CODEX_MODEL", settings.e2e_llm_model),
            cwd=cwd,
            approval_policy=os.environ.get("CODEX_APPROVAL_POLICY", "never"),
            approval_mode=cast(
                Any,
                _safe_approval_mode(
                    adapter_name="Codex",
                    env_var="CODEX_APPROVAL_MODE",
                    default="manual",
                ),
            ),
            custom_section="Keep responses short and direct.",
            enable_task_events=False,
            enable_execution_reporting=False,
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


def create_letta_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Letta adapter backed by Letta Cloud or a self-hosted server."""
    pytest.importorskip("letta_client", reason="letta-client not installed")

    base_url = os.environ.get("LETTA_BASE_URL", "https://api.letta.com")
    provider_key = os.environ.get("LETTA_API_KEY")
    mcp_server_url = os.environ.get("MCP_SERVER_URL")

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
            project=os.environ.get("LETTA_PROJECT"),
            model=os.environ.get("LETTA_MODEL", "openai/gpt-5.4-mini"),
            mcp_server_url=mcp_server_url,
            custom_section="Keep responses short and concise.",
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )


# =============================================================================
# Baseline Default Adapter Factories
# =============================================================================


def create_baseline_default_langgraph_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered LangGraph adapter for baseline live proof."""
    _require_openai_key()
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
    )


def create_baseline_default_anthropic_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Anthropic adapter for baseline live proof."""
    _require_anthropic_key()
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(model=settings.e2e_anthropic_model)


def create_baseline_default_pydantic_ai_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Pydantic AI adapter for baseline live proof."""
    _require_openai_key()
    from band.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(model=f"openai:{settings.e2e_llm_model}")


def create_baseline_default_claude_sdk_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Claude SDK adapter for baseline live proof."""
    _require_anthropic_key()
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(model=settings.e2e_anthropic_model)


def create_baseline_default_gemini_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an unsteered Gemini adapter for baseline live proof."""
    _require_gemini_key_or_vertex()
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(model=os.environ.get("E2E_GEMINI_MODEL", "gemini-2.5-flash"))


def create_baseline_default_google_adk_adapter(
    settings: E2ESettings,
) -> SimpleAdapter[Any]:
    """Create an unsteered Google ADK adapter for baseline live proof."""
    del settings
    _require_gemini_key_or_vertex()
    from band.adapters.google_adk import GoogleADKAdapter

    return GoogleADKAdapter(
        model=os.environ.get("E2E_GEMINI_MODEL", "gemini-2.5-flash")
    )


# =============================================================================
# Adapter Registry
# =============================================================================

ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "langgraph": create_langgraph_adapter,
    "anthropic": create_anthropic_adapter,
    "pydantic_ai": create_pydantic_ai_adapter,
    "claude_sdk": create_claude_sdk_adapter,
    "crewai": create_crewai_adapter,
    "crewai_flow": create_crewai_flow_adapter,
    "opencode": create_opencode_adapter,
    "codex": create_codex_adapter,
    "letta": create_letta_adapter,
}

BASELINE_DEFAULT_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "langgraph": create_baseline_default_langgraph_adapter,
    "anthropic": create_baseline_default_anthropic_adapter,
    "pydantic_ai": create_baseline_default_pydantic_ai_adapter,
    "claude_sdk": create_baseline_default_claude_sdk_adapter,
    "gemini": create_baseline_default_gemini_adapter,
    "google_adk": create_baseline_default_google_adk_adapter,
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
BASELINE_L0_ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "langgraph": create_baseline_default_langgraph_adapter,
    "anthropic": create_baseline_default_anthropic_adapter,
    "pydantic_ai": create_baseline_default_pydantic_ai_adapter,
    "claude_sdk": create_baseline_default_claude_sdk_adapter,
    "gemini": create_baseline_default_gemini_adapter,
    "google_adk": create_baseline_default_google_adk_adapter,
    "opencode": create_opencode_adapter,
    "codex": create_codex_adapter,
    "letta": create_letta_adapter,
}
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
