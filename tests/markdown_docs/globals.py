"""Namespace merged into every pytest-markdown-docs `` ```python `` fence.

``pytest_markdown_docs_globals()`` in root ``conftest.py`` returns ``build_globals()`` —
names pre-bound so partial doc snippets can omit imports and setup.

Layers:
- ``_sdk_symbols()``: real SDK types (``AnthropicAdapter``, ``Emit``, …)
- ``_langgraph_symbols()``: LangGraph placeholders (``llm``, ``checkpointer``, …)
- ``_fixture_doubles()``: test doubles (``Agent``, ``AnyAdapter``, ``adapter``, ``os``)

Env/HTTP/asyncio mocking and ``fixture:*`` hooks live in ``fixtures.py``.
On ``NameError``, add the missing name to the matching layer.
"""

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
    """``Agent`` double; ``create`` fills placeholder ``agent_id`` / ``api_key``."""

    @staticmethod
    def create(**kwargs: object) -> object:
        kwargs.setdefault("agent_id", MARKDOWN_AGENT_ID)
        kwargs.setdefault("api_key", MARKDOWN_API_KEY)
        return BandAgent.create(**kwargs)

    @staticmethod
    def from_config(*args: object, **kwargs: object) -> object:
        return BandAgent.from_config(*args, **kwargs)


class AnyAdapter:
    """Generic adapter stub that only records ``kwargs`` (migration doc examples)."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class MarkdownCalculatorGraph:
    """Minimal graph double for `graph_as_tool()` documentation snippets."""

    async def ainvoke(
        self, inputs: dict[str, object], config: dict[str, object] | None = None
    ) -> dict[str, object]:
        return {**inputs, "result": 0}


def create_calculator_graph() -> MarkdownCalculatorGraph:
    """Return a graph-like object for markdown code-fence execution."""
    return MarkdownCalculatorGraph()


def _sdk_symbols() -> dict[str, object]:
    """Real SDK types for fences that skip imports."""
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
    """LangGraph placeholders for adapter doc fences."""
    return {
        "checkpointer": object(),
        "create_calculator_graph": create_calculator_graph,
        "llm": object(),
    }


def _fixture_doubles() -> dict[str, object]:
    """Doubles and pre-built values snippets assume already exist."""
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
    """Merge the three layers for ``pytest_markdown_docs_globals()``."""
    return {
        **_sdk_symbols(),
        **_langgraph_symbols(),
        **_fixture_doubles(),
    }
