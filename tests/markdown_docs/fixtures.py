from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.markdown_docs.assertions import (
    assert_contact_respond_method_exists,
    assert_omit_vs_null_calls,
    assert_rest_pattern_methods_exist,
)
from tests.markdown_docs.constants import (
    MARKDOWN_AGENT_ID,
    MARKDOWN_API_KEY,
    MARKDOWN_RESEARCHER_AGENT_ID,
    MARKDOWN_REST_URL,
    MARKDOWN_WS_URL,
)
from tests.markdown_docs.hooks import markdown_docs_enabled
from tests.markdown_docs.offline_rest import stub_offline_rest


@pytest.fixture
def markdown_link():
    """Real ThenvoiLink with offline REST transport for markdown snippets."""
    from thenvoi.platform.link import ThenvoiLink

    platform_link = ThenvoiLink(
        agent_id=MARKDOWN_AGENT_ID,
        api_key=MARKDOWN_API_KEY,
        rest_url=MARKDOWN_REST_URL,
        ws_url=MARKDOWN_WS_URL,
    )
    stub_offline_rest(platform_link.rest)
    setattr(
        platform_link,
        "assert_rest_pattern_methods_exist",
        lambda: assert_rest_pattern_methods_exist(platform_link),
    )
    return platform_link


@pytest.fixture
def markdown_client():
    """Real AsyncRestClient with offline transport for markdown snippets."""
    from thenvoi.client.rest import AsyncRestClient

    rest_client = AsyncRestClient(
        api_key=MARKDOWN_API_KEY,
        base_url=MARKDOWN_REST_URL,
    )
    stub_offline_rest(rest_client)
    setattr(
        rest_client,
        "assert_contact_respond_method_exists",
        lambda: assert_contact_respond_method_exists(rest_client),
    )
    setattr(
        rest_client,
        "assert_omit_vs_null_calls",
        lambda: assert_omit_vs_null_calls(rest_client),
    )
    return rest_client


@pytest.fixture(autouse=True)
def _noop_asyncio_run_for_markdown_docs(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip asyncio.run() in markdown quick-starts that would hit the live platform."""
    if not markdown_docs_enabled(request.config):
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
def markdown_agent_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary agent_config.yaml for markdown Agent.from_config snippets."""
    from thenvoi import Agent

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
