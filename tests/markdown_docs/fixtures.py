"""Fixtures used by pytest-markdown-docs code fences."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band import Agent
from band.config import loader
from tests.markdown_docs.globals import (
    MARKDOWN_AGENT_ID,
    MARKDOWN_API_KEY,
    MARKDOWN_RESEARCHER_AGENT_ID,
)


def _markdown_docs_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("markdowndocs", default=False))


def _seed_markdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope dummy keys to each markdown code-fence test."""
    monkeypatch.setenv("OPENAI_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", MARKDOWN_API_KEY)
    monkeypatch.setenv("QUICKSTART_AGENT_ID", MARKDOWN_AGENT_ID)
    monkeypatch.setenv("QUICKSTART_API_KEY", MARKDOWN_API_KEY)


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

    monkeypatch.setattr(asyncio, "run", noop_run)


@pytest.fixture
def agent_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Back `fixture:agent_config_path` snippets with temporary credentials."""

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
