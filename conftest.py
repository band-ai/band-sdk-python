"""Repo-root pytest hooks for markdown doc snippet tests."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from thenvoi.client.rest import AsyncRestClient
    from thenvoi.platform.link import ThenvoiLink


def _markdown_docs_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("markdowndocs", default=False))


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Suppress expected DeprecationWarnings in markdown doc snippet tests."""
    if not _markdown_docs_enabled(config):
        return

    for item in items:
        if item.get_closest_marker("markdown-docs"):
            item.add_marker(pytest.mark.filterwarnings("ignore::DeprecationWarning"))


def _stub_offline_rest(client: AsyncRestClient) -> list[dict]:
    """Attach an offline HTTP stub to a real AsyncRestClient.

    Only the low-level httpx transport is replaced. Namespace clients and
    method signatures remain the generated Fern implementations.
    """
    captured_json: list[dict] = []

    async def fake_request(*args: object, **kwargs: object) -> object:
        path = str(args[0]) if args else ""
        body = kwargs.get("json")
        if isinstance(body, dict):
            captured_json.append(body)

        now = datetime.now(timezone.utc).isoformat()
        if "participants" in path:
            payload = {
                "data": [],
                "metadata": {
                    "page": 1,
                    "page_size": 50,
                    "total_count": 0,
                    "total_pages": 0,
                },
            }
        elif "/messages" in path:
            payload = {
                "data": {
                    "id": "msg-1",
                    "success": True,
                    "recipients": [],
                    "inserted_at": now,
                    "updated_at": now,
                }
            }
        elif "respond" in path:
            payload = {
                "data": {
                    "id": "req-1",
                    "status": "approved",
                    "inserted_at": now,
                    "updated_at": now,
                }
            }
        else:
            payload = {"data": {"id": "room-1", "inserted_at": now, "updated_at": now}}

        class _Response:
            status_code = 200

            def json(self) -> dict:
                return payload

        return _Response()

    client._client_wrapper.httpx_client.request = AsyncMock(side_effect=fake_request)
    client._markdown_captured_json = captured_json
    return captured_json


def _assert_rest_pattern_methods_exist(link: ThenvoiLink) -> None:
    """Assert the documented Fern namespace methods exist on a real link."""
    import inspect

    assert inspect.iscoroutinefunction(link.rest.agent_api_chats.create_agent_chat)
    assert inspect.iscoroutinefunction(
        link.rest.agent_api_messages.create_agent_chat_message
    )
    assert inspect.iscoroutinefunction(
        link.rest.agent_api_participants.list_agent_chat_participants
    )


def _assert_contact_respond_method_exists(client: AsyncRestClient) -> None:
    """Assert the documented contact response method exists on a real client."""
    import inspect

    assert inspect.iscoroutinefunction(
        client.agent_api_contacts.respond_to_agent_contact_request
    )


def _assert_omit_vs_null_calls(client: AsyncRestClient) -> None:
    """Assert the markdown snippet demonstrated null vs omitted Fern fields."""
    calls = client._markdown_captured_json
    assert calls[0]["handle"] is None
    assert calls[1]["handle"] is Ellipsis  # Fern OMIT sentinel, not sent as null


class _MarkdownAgentFactory:
    """Doc-test proxy that supplies placeholder credentials for Agent.create."""

    @staticmethod
    def create(**kwargs: object) -> object:
        from thenvoi import Agent

        kwargs.setdefault("agent_id", "markdown-docs-agent")
        kwargs.setdefault("api_key", "markdown-docs-test")
        return Agent.create(**kwargs)

    @staticmethod
    def from_config(*args: object, **kwargs: object) -> object:
        from thenvoi import Agent

        return Agent.from_config(*args, **kwargs)


class _AnyAdapter:
    """Generic adapter placeholder for universal migration snippets."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture
def markdown_link() -> ThenvoiLink:
    """Real ThenvoiLink with offline REST transport for markdown snippets."""
    from thenvoi.platform.link import ThenvoiLink

    platform_link = ThenvoiLink(
        agent_id="markdown-docs-agent",
        api_key="markdown-docs-test",
        rest_url="https://example.test",
        ws_url="wss://example.test/socket",
    )
    _stub_offline_rest(platform_link.rest)
    setattr(
        platform_link,
        "assert_rest_pattern_methods_exist",
        lambda: _assert_rest_pattern_methods_exist(platform_link),
    )
    return platform_link


@pytest.fixture
def markdown_client() -> AsyncRestClient:
    """Real AsyncRestClient with offline transport for markdown snippets."""
    from thenvoi.client.rest import AsyncRestClient

    rest_client = AsyncRestClient(
        api_key="markdown-docs-test",
        base_url="https://example.test",
    )
    _stub_offline_rest(rest_client)
    setattr(
        rest_client,
        "assert_contact_respond_method_exists",
        lambda: _assert_contact_respond_method_exists(rest_client),
    )
    setattr(
        rest_client,
        "assert_omit_vs_null_calls",
        lambda: _assert_omit_vs_null_calls(rest_client),
    )
    return rest_client


@pytest.fixture(autouse=True)
def _noop_asyncio_run_for_markdown_docs(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip asyncio.run() in markdown quick-starts that would hit the live platform."""
    if not _markdown_docs_enabled(request.config):
        return
    if request.node.get_closest_marker("markdown-docs") is None:
        return

    def noop_run(coro: object) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None

    monkeypatch.setattr(asyncio, "run", noop_run)


@pytest.fixture
def markdown_agent_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Temporary agent_config.yaml for markdown Agent.from_config snippets."""
    from thenvoi import Agent

    async def run_noop(self: Agent) -> None:
        return None

    monkeypatch.setattr(Agent, "run", run_noop)

    path = tmp_path / "agent_config.yaml"
    path.write_text(
        "planner:\n"
        "  agent_id: markdown-docs-agent\n"
        "  api_key: markdown-docs-test\n"
        "researcher:\n"
        "  agent_id: markdown-docs-researcher\n"
        "  api_key: markdown-docs-test\n"
    )
    return path


def pytest_markdown_docs_globals() -> dict[str, object]:
    """Inject shared names and dummy API keys for pytest-markdown-docs snippets."""
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from thenvoi import AdapterFeatures, Capability, Emit, ThenvoiConfigError
    from thenvoi.adapters import AnthropicAdapter, ClaudeSDKAdapter, GeminiAdapter
    from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig
    from thenvoi.client.rest import ChatMessageRequest, ChatRoomRequest
    from thenvoi.platform.event import ContactRequestReceivedEvent
    from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy
    from typing_extensions import TypedDict

    os.environ.setdefault("OPENAI_API_KEY", "markdown-docs-test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "markdown-docs-test")
    os.environ.setdefault("QUICKSTART_AGENT_ID", "markdown-docs-agent")
    os.environ.setdefault("QUICKSTART_API_KEY", "markdown-docs-test")

    class _CalcState(TypedDict):
        result: int

    def _add(state: _CalcState) -> _CalcState:
        return {"result": 0}

    def create_calculator_graph():
        graph = StateGraph(_CalcState)
        graph.add_node("add", _add)
        graph.add_edge(START, "add")
        graph.add_edge("add", END)
        return graph.compile()

    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5",
        api_key="markdown-docs-test",
    )

    return {
        "Agent": _MarkdownAgentFactory,
        "AdapterFeatures": AdapterFeatures,
        "AnthropicAdapter": AnthropicAdapter,
        "AnyAdapter": _AnyAdapter,
        "Capability": Capability,
        "ClaudeSDKAdapter": ClaudeSDKAdapter,
        "CodexAdapter": CodexAdapter,
        "CodexAdapterConfig": CodexAdapterConfig,
        "Emit": Emit,
        "GeminiAdapter": GeminiAdapter,
        "ThenvoiConfigError": ThenvoiConfigError,
        "adapter": adapter,
        "llm": ChatOpenAI(model="gpt-4o-mini", api_key="markdown-docs-test"),
        "checkpointer": InMemorySaver(),
        "my_tools": [],
        "create_calculator_graph": create_calculator_graph,
        "ChatMessageRequest": ChatMessageRequest,
        "ChatRoomRequest": ChatRoomRequest,
        "ContactEventConfig": ContactEventConfig,
        "ContactEventStrategy": ContactEventStrategy,
        "ContactRequestReceivedEvent": ContactRequestReceivedEvent,
        "os": os,
        "pytest": pytest,
    }
