"""Adapter factory fixtures for E2E tests.

Provides factory functions that create real adapter instances configured
with cheap LLM models for E2E testing. Each factory returns an adapter
ready to be used with Agent.create().

Run with:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/adapters/ -v -s --no-cov
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import pytest

from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Emit

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


def create_langgraph_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a LangGraph adapter with a cheap OpenAI model."""
    _require_openai_key()
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from thenvoi.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(model=settings.e2e_llm_model),
        checkpointer=MemorySaver(),
        custom_section="Keep responses short and concise. Always respond using the thenvoi_send_message tool.",
    )


def create_anthropic_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create an Anthropic adapter with a cheap Claude model."""
    _require_anthropic_key()
    from thenvoi.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and concise.",
    )


def create_pydantic_ai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Pydantic AI adapter with a cheap OpenAI model."""
    _require_openai_key()
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

    return PydanticAIAdapter(
        model=f"openai:{settings.e2e_llm_model}",
        custom_section="Keep responses short and concise.",
    )


def create_claude_sdk_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a Claude SDK adapter with a cheap Claude model."""
    _require_anthropic_key()
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=settings.e2e_anthropic_model,
        custom_section="Keep responses short and concise.",
    )


def create_crewai_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI adapter with a cheap OpenAI model."""
    _require_openai_key()
    from thenvoi.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=settings.e2e_llm_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing",
        backstory="A test agent for E2E validation.",
        custom_section="Keep responses short and concise.",
    )


def create_crewai_flow_adapter(settings: E2ESettings) -> SimpleAdapter[Any]:
    """Create a CrewAI Flow adapter whose real side-effect path sends a reply."""
    from thenvoi.adapters.crewai_flow import CrewAIFlowAdapter

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

    from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=base_url,
            provider_id=os.environ.get("OPENCODE_PROVIDER_ID", "opencode"),
            model_id=os.environ.get("OPENCODE_MODEL_ID", "minimax-m2.5-free"),
            agent=os.environ.get("OPENCODE_AGENT") or None,
            custom_section="Keep responses short and concise.",
            approval_mode="auto_accept",
            question_mode="auto_reject",
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

    from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

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
    "letta": create_letta_adapter,
}

# Note: CrewAI Flow and Parlant are excluded from the default parametrized set.
# CrewAI Flow proves terminal-return side effects in a dedicated file, and
# Parlant requires a running server plus adapter-specific setup.


# Note: The parametrized `adapter_entry` fixture lives in tests/e2e/conftest.py
# so it is shared between adapters/ and scenarios/ tests.
