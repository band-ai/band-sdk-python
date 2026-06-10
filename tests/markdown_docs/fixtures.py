"""Fixtures used by pytest-markdown-docs code fences."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.markdown_docs.globals import (
    MARKDOWN_AGENT_ID,
    MARKDOWN_API_KEY,
    MARKDOWN_RESEARCHER_AGENT_ID,
    MARKDOWN_REST_URL,
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


def _stub_offline_rest(client: object) -> list[dict[str, object]]:
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

    client._client_wrapper.httpx_client.request = AsyncMock(side_effect=fake_request)
    client._markdown_captured_json = captured_json
    return captured_json


def _seed_markdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope dummy keys to each markdown code-fence test."""
    monkeypatch.setenv("OPENAI_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("QUICKSTART_AGENT_ID", MARKDOWN_AGENT_ID)
    monkeypatch.setenv("QUICKSTART_API_KEY", MARKDOWN_API_KEY)


@pytest.fixture
def client():
    """Back `fixture:client` snippets with a generated client and fake HTTP."""
    from band.client.rest import AsyncRestClient

    rest_client = AsyncRestClient(
        api_key=MARKDOWN_API_KEY,
        base_url=MARKDOWN_REST_URL,
    )
    _stub_offline_rest(rest_client)
    assert inspect.iscoroutinefunction(
        rest_client.agent_api_contacts.respond_to_agent_contact_request
    )
    yield rest_client
    if len(rest_client._markdown_captured_json) == 2:
        # The REST docs compare explicit null with Fern's OMIT sentinel.
        calls = rest_client._markdown_captured_json
        assert calls[0]["handle"] is None
        assert calls[1]["handle"] is Ellipsis  # Fern OMIT sentinel, not sent as null


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
    return path
