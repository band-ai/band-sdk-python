"""Globals injected into pytest-markdown-docs code-fence execution."""

from __future__ import annotations

import os

from band import Agent as BandAgent
from band import AdapterFeatures, BandConfigError, Capability, Emit
from band.adapters import AnthropicAdapter, ClaudeSDKAdapter, GeminiAdapter
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.platform.event import ContactRequestReceivedEvent
from band.runtime.types import ContactEventConfig, ContactEventStrategy

MARKDOWN_AGENT_ID = "markdown-docs-agent"
MARKDOWN_RESEARCHER_AGENT_ID = "markdown-docs-researcher"
MARKDOWN_API_KEY = "markdown-docs-test"
MARKDOWN_REST_URL = "https://example.test"


class MarkdownAgentFactory:
    """Doc-test proxy that supplies placeholder credentials for Agent.create."""

    @staticmethod
    def create(**kwargs: object) -> object:
        kwargs.setdefault("agent_id", MARKDOWN_AGENT_ID)
        kwargs.setdefault("api_key", MARKDOWN_API_KEY)
        return BandAgent.create(**kwargs)

    @staticmethod
    def from_config(*args: object, **kwargs: object) -> object:
        return BandAgent.from_config(*args, **kwargs)


class AnyAdapter:
    """Generic adapter placeholder for migration snippets."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _sdk_symbols() -> dict[str, object]:
    """SDK names used by partial snippets without local imports."""
    return {
        "AdapterFeatures": AdapterFeatures,
        "AnthropicAdapter": AnthropicAdapter,
        "Capability": Capability,
        "ClaudeSDKAdapter": ClaudeSDKAdapter,
        "CodexAdapter": CodexAdapter,
        "CodexAdapterConfig": CodexAdapterConfig,
        "Emit": Emit,
        "GeminiAdapter": GeminiAdapter,
        "BandConfigError": BandConfigError,
        "ContactEventConfig": ContactEventConfig,
        "ContactEventStrategy": ContactEventStrategy,
        "ContactRequestReceivedEvent": ContactRequestReceivedEvent,
    }


def _langgraph_symbols() -> dict[str, object]:
    """Minimal objects needed by README LangGraph snippets."""
    return {"llm": object()}


def _fixture_doubles() -> dict[str, object]:
    """Shared stand-ins for snippets that assume surrounding setup."""
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5",
        api_key=MARKDOWN_API_KEY,
    )
    return {
        "Agent": MarkdownAgentFactory,
        "AnyAdapter": AnyAdapter,
        "adapter": adapter,
        "os": os,
    }


def build_globals() -> dict[str, object]:
    """Return the namespace consumed by pytest_markdown_docs_globals()."""
    return {
        **_sdk_symbols(),
        **_langgraph_symbols(),
        **_fixture_doubles(),
    }
