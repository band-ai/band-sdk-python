"""Repo-root pytest hooks for markdown doc snippet tests."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from thenvoi.client.rest import AsyncRestClient
    from thenvoi.platform.link import ThenvoiLink


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


@pytest.fixture
def link() -> ThenvoiLink:
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
def client() -> AsyncRestClient:
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


def pytest_markdown_docs_globals() -> dict[str, object]:
    """Inject shared names and dummy API keys for pytest-markdown-docs snippets."""
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from thenvoi.client.rest import ChatMessageRequest, ChatRoomRequest
    from typing_extensions import TypedDict

    os.environ.setdefault("OPENAI_API_KEY", "markdown-docs-test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "markdown-docs-test")

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

    return {
        "llm": ChatOpenAI(model="gpt-4o-mini", api_key="markdown-docs-test"),
        "checkpointer": InMemorySaver(),
        "my_tools": [],
        "create_calculator_graph": create_calculator_graph,
        "ChatMessageRequest": ChatMessageRequest,
        "ChatRoomRequest": ChatRoomRequest,
    }
