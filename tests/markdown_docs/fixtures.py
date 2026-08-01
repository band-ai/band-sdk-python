"""Fixtures used by pytest-markdown-docs code fences."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.markdown_docs.globals import (
    MARKDOWN_AGENT_ID,
    MARKDOWN_API_KEY,
    MARKDOWN_RESEARCHER_AGENT_ID,
    MARKDOWN_REST_URL,
    MARKDOWN_ROOM_ID,
)


def _markdown_docs_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("markdowndocs", default=False))


def _payload_for_path(path: str, now: str) -> dict[str, object]:
    """Return the smallest Fern-shaped response each snippet needs."""
    if "respond" in path:
        return {
            "data": {
                "id": "req-1",
                "status": "approved",
                "inserted_at": now,
                "updated_at": now,
            }
        }
    return {"data": {"id": "room-1", "inserted_at": now, "updated_at": now}}


def _stub_offline_rest(
    client: object, monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, object]]:
    """Patch only HTTP I/O so snippets still exercise generated REST methods."""
    captured_json: list[dict[str, object]] = []

    async def fake_request(*args: object, **kwargs: object) -> object:
        path = str(args[0]) if args else ""
        body = kwargs.get("json")
        if isinstance(body, dict):
            captured_json.append(body)

        payload = _payload_for_path(path, datetime.now(timezone.utc).isoformat())

        class _Response:
            status_code = 200

            def json(self) -> dict[str, object]:
                return payload

        return _Response()

    monkeypatch.setattr(
        client._client_wrapper.httpx_client,
        "request",
        AsyncMock(side_effect=fake_request),
    )
    return captured_json


def _seed_markdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope dummy keys to each markdown code-fence test."""
    monkeypatch.setenv("OPENAI_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("QUICKSTART_AGENT_ID", MARKDOWN_AGENT_ID)
    monkeypatch.setenv("QUICKSTART_API_KEY", MARKDOWN_API_KEY)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """Back `fixture:client` snippets with a generated client and fake HTTP."""
    from band.client.rest import AsyncRestClient

    # Use the generated client so docs fail if Fern namespaces drift.
    rest_client = AsyncRestClient(
        api_key=MARKDOWN_API_KEY,
        base_url=MARKDOWN_REST_URL,
    )
    captured_json = _stub_offline_rest(rest_client, monkeypatch)
    assert inspect.iscoroutinefunction(
        rest_client.agent_api_contacts.respond_to_agent_contact_request
    )
    yield rest_client
    if len(captured_json) == 2:
        # The OMIT-vs-null snippet should send null first, then Fern's OMIT sentinel.
        assert captured_json[0]["handle"] is None
        assert captured_json[1]["handle"] is Ellipsis


@pytest.fixture(autouse=True)
def _prepare_markdown_docs_runtime(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seed env and prevent quickstarts from opening platform connections."""
    if not _markdown_docs_enabled(request.config):
        return
    if request.node.get_closest_marker("markdown-docs") is None:
        return

    _seed_markdown_env(monkeypatch)

    def noop_run(coro: object) -> None:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None

    monkeypatch.setattr(asyncio, "run", noop_run)


@pytest.fixture
def agent_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Back `fixture:agent_config_path` snippets with temporary credentials."""
    from band import Agent
    from band.config import loader

    async def run_noop(self: Agent) -> None:
        return None

    monkeypatch.setattr(Agent, "run", run_noop)

    path = tmp_path / "agent_config.yaml"
    path.write_text(
        f"planner:\n"
        f"  agent_id: {MARKDOWN_AGENT_ID}\n"
        f"  api_key: {MARKDOWN_API_KEY}\n"
        f"researcher:\n"
        f"  agent_id: {MARKDOWN_RESEARCHER_AGENT_ID}\n"
        f"  api_key: {MARKDOWN_API_KEY}\n"
    )
    monkeypatch.setattr(loader, "get_config_path", lambda: path)
    return path


@pytest.fixture
def room_tools():
    """Back `fixture:room_tools` snippets with in-memory agent tools."""
    from band.testing import FakeAgentTools

    return FakeAgentTools(room_id=MARKDOWN_ROOM_ID)


@pytest.fixture
def turn_input():
    """One turn's input, for snippets that drive a backend directly."""
    from band.core.types import PlatformMessage
    from band.testing import FakeAgentTools
    from tests.core.adapterhelpers import make_agent_input

    return make_agent_input(
        tools=FakeAgentTools(room_id=MARKDOWN_ROOM_ID),
        room_id=MARKDOWN_ROOM_ID,
        msg=PlatformMessage(
            id="msg-1",
            room_id=MARKDOWN_ROOM_ID,
            content="Say hello to the room.",
            sender_id="user-1",
            sender_type="User",
            sender_name="Ana",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        ),
    )


@pytest.fixture
def turn_adapter():
    """An adapter whose model posts to the room, then answers.

    Scripted rather than mocked at the transport: the tool round is what makes
    a turn emit observable events, so a snippet about the observation stream
    needs one to have anything to show.
    """
    from band.core.backends.native import NativeToolLoopBackend
    from band.core.contracts import ModelResponse, ModelToolCall
    from tests.core.contractsupport import NativeLoopAdapter

    rounds = iter(
        [
            ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        id="call-1",
                        name="band_send_message",
                        arguments={"content": "Hello @Ana", "mentions": ["user-1"]},
                    )
                ]
            ),
            ModelResponse(text="Said hello."),
        ]
    )

    class ScriptedProvider:
        """A ``ModelProvider`` that replays the rounds above."""

        async def complete(self, request: object, *, context: object) -> object:
            return next(rounds)

        def default_history_policy(self):
            from band.core.backends.history import DefaultHistoryPolicy

            return DefaultHistoryPolicy()

    return NativeLoopAdapter(NativeToolLoopBackend(provider=ScriptedProvider()))


@pytest.fixture
def scripted_anthropic_adapter():
    """Build an `AnthropicAdapter` whose SDK client replays scripted replies.

    Returns `(adapter, client)`; the client records every payload the provider
    sent, which is what makes the snippet's assertion about the real request
    shape possible.
    """
    from band.adapters.anthropic import AnthropicAdapter
    from tests.modelclients import ScriptedAnthropicClient, anthropic_reply

    def build(*texts: str):
        adapter = AnthropicAdapter()
        client = ScriptedAnthropicClient([anthropic_reply(text) for text in texts])
        adapter.client = client
        return adapter, client

    return build


@pytest.fixture
def logging_sandbox():
    """Let a snippet configure process logging, then put it back.

    `configure_logging` applies its config with `dictConfig`, which replaces
    the root logger's handlers — so a snippet that runs for real would leak
    its setup into every test after it.
    """
    from tests.logsupport import restored_logging

    with restored_logging():
        yield


def _mock_gateway_runtime() -> AsyncMock:
    """Runtime that satisfies ``Agent.start`` / ``stop`` without dialing Band."""
    runtime = AsyncMock()
    runtime.agent_id = MARKDOWN_AGENT_ID
    runtime.agent_name = "markdown-gateway"
    runtime.agent_description = "markdown-docs gateway agent"
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    runtime.initialize = AsyncMock()
    return runtime


@pytest.fixture
def a2a_agent(monkeypatch: pytest.MonkeyPatch):
    """Mocked ``Agent`` + ``A2AGatewayAdapter``; HTTP ``serve()`` returns immediately.

    Peer discovery is stubbed and ``GatewayServer`` is faked so a gateway-host
    snippet can ``async with A2AGateway(...): await gateway.serve()`` offline.
    """
    from band.agent import Agent
    from band.integrations.a2a.gateway import A2AGatewayAdapter

    adapter = A2AGatewayAdapter(api_key=MARKDOWN_API_KEY, port=10000)
    empty = MagicMock()
    empty.data = []
    adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(return_value=empty)

    mock_server = MagicMock()
    mock_server.start = AsyncMock()
    mock_server.serve = AsyncMock()
    mock_server.stop = AsyncMock()
    monkeypatch.setattr(
        "band.integrations.a2a.gateway.adapter.GatewayServer",
        MagicMock(return_value=mock_server),
    )

    agent = Agent(runtime=_mock_gateway_runtime(), adapter=adapter)
    yield agent


@pytest.fixture
def acp_agent(monkeypatch: pytest.MonkeyPatch):
    """Mocked ``Agent`` + ``BandACPServerAdapter``; ``acp.run_agent`` is a no-op."""
    from band.agent import Agent
    from band.integrations.acp.server_adapter import BandACPServerAdapter

    adapter = BandACPServerAdapter(rest_url=MARKDOWN_REST_URL, api_key=MARKDOWN_API_KEY)
    adapter.close = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr("acp.run_agent", AsyncMock())

    agent = Agent(runtime=_mock_gateway_runtime(), adapter=adapter)
    yield agent


@pytest.fixture
def acp_server(acp_agent):
    """ACP protocol server wired to the mocked ``acp_agent`` adapter."""
    from band.integrations.acp.server import ACPServer

    return ACPServer(acp_agent.adapter)


@pytest.fixture
def slack_agent(monkeypatch: pytest.MonkeyPatch):
    """Mocked ``Agent`` + ``SlackAdapter``; transport ``serve`` returns immediately.

    Socket Mode's real serve waits on a stop signal; gateway-host snippets only
    need the ``async with SlackGateway(...): await serve()`` spelling to run.
    """
    from band.agent import Agent
    from band.core.simple_adapter import SimpleAdapter
    from band.integrations.slack.adapter import SlackAdapter
    from band.integrations.slack.host import SlackGateway
    from band.integrations.slack.types import SlackApp

    class _Brain(SimpleAdapter[object]):
        async def on_message(self, *args: object, **kwargs: object) -> None:
            return None

    adapter = SlackAdapter(
        inner=_Brain(),  # type: ignore[arg-type]
        apps=[
            SlackApp(
                slug="markdown",
                bot_token="xoxb-test",
                signing_secret="",
                app_token="xapp-test",
            )
        ],
        api_key=MARKDOWN_API_KEY,
        transport="socket",
        rest_client=MagicMock(),
    )
    adapter.start_ingress = AsyncMock()  # type: ignore[method-assign]
    adapter.close = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(SlackGateway, "_serve_transport", AsyncMock())

    agent = Agent(runtime=_mock_gateway_runtime(), adapter=adapter)
    yield agent
