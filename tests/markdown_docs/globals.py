from __future__ import annotations

import os

import pytest
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi import AdapterFeatures, Capability, Emit, ThenvoiConfigError
from thenvoi.adapters import AnthropicAdapter, ClaudeSDKAdapter, GeminiAdapter
from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig
from thenvoi.client.rest import ChatMessageRequest, ChatRoomRequest
from thenvoi.platform.event import ContactRequestReceivedEvent
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

from tests.markdown_docs.constants import (
    MARKDOWN_AGENT_ID,
    MARKDOWN_API_KEY,
)
from tests.markdown_docs.doubles import AnyAdapter, MarkdownAgentFactory
from tests.markdown_docs.langgraph_stub import create_calculator_graph


def _seed_env() -> None:
    os.environ.setdefault("OPENAI_API_KEY", MARKDOWN_API_KEY)
    os.environ.setdefault("ANTHROPIC_API_KEY", MARKDOWN_API_KEY)
    os.environ.setdefault("QUICKSTART_AGENT_ID", MARKDOWN_AGENT_ID)
    os.environ.setdefault("QUICKSTART_API_KEY", MARKDOWN_API_KEY)


def _sdk_symbols() -> dict[str, object]:
    return {
        "AdapterFeatures": AdapterFeatures,
        "AnthropicAdapter": AnthropicAdapter,
        "Capability": Capability,
        "ClaudeSDKAdapter": ClaudeSDKAdapter,
        "CodexAdapter": CodexAdapter,
        "CodexAdapterConfig": CodexAdapterConfig,
        "Emit": Emit,
        "GeminiAdapter": GeminiAdapter,
        "ThenvoiConfigError": ThenvoiConfigError,
        "ChatMessageRequest": ChatMessageRequest,
        "ChatRoomRequest": ChatRoomRequest,
        "ContactEventConfig": ContactEventConfig,
        "ContactEventStrategy": ContactEventStrategy,
        "ContactRequestReceivedEvent": ContactRequestReceivedEvent,
    }


def _langgraph_symbols() -> dict[str, object]:
    return {
        "llm": ChatOpenAI(model="gpt-4o-mini", api_key=MARKDOWN_API_KEY),
        "checkpointer": InMemorySaver(),
        "my_tools": [],
        "create_calculator_graph": create_calculator_graph,
    }


def _fixture_doubles() -> dict[str, object]:
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5",
        api_key=MARKDOWN_API_KEY,
    )
    return {
        "Agent": MarkdownAgentFactory,
        "AnyAdapter": AnyAdapter,
        "adapter": adapter,
        "os": os,
        "pytest": pytest,
    }


def build_globals() -> dict[str, object]:
    """Inject shared names and dummy API keys for pytest-markdown-docs snippets."""
    _seed_env()
    return {
        **_sdk_symbols(),
        **_langgraph_symbols(),
        **_fixture_doubles(),
    }
