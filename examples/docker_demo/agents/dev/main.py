"""Lead Developer agent (Codex) for the Band Docker demo.

The kit launcher execs this file with the workspace's own locked venv; Band
identity and endpoints arrive as BAND_* env vars. This workspace's image must
also carry the ``codex`` CLI, and the OpenAI credential is provided host-side
(``sbx secret set -g openai``) so it never enters this VM.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.prompts.roles import CONVERSATION_DISCIPLINE
from band.runtime.shutdown import run_with_graceful_shutdown


class Identity(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAND_", case_sensitive=False, env_ignore_empty=True
    )

    agent_id: str
    api_key: str
    ws_url: str
    rest_url: str


class DevConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEV_", case_sensitive=False, env_ignore_empty=True
    )

    model: str | None = None


def build_persona() -> str:
    persona = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")
    return f"{persona}\n\n{CONVERSATION_DISCIPLINE}"


def expose_llm_key() -> None:
    """Copy the sbx-injected placeholder into the var the codex CLI reads.

    sbx reserves OPENAI_API_KEY for its wire-only injection (empty in the VM),
    so the launcher injects the placeholder under OPENAI_PROXY_KEY; the real key
    stays on the host and the proxy swaps the placeholder on the wire.
    """
    if proxy := os.environ.get("OPENAI_PROXY_KEY"):
        os.environ["OPENAI_API_KEY"] = proxy


def login_codex() -> None:
    """`codex app-server` authenticates from a stored login, not OPENAI_API_KEY —
    so log in with the placeholder key (the proxy swaps it for the real one on
    the wire). Without this, codex requests go out with no auth header (401)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return
    result = subprocess.run(
        ["codex", "login", "--with-api-key"], input=key, text=True, capture_output=True
    )
    if result.returncode != 0:
        # Fail here, not later with a confusing 401. Redact the key from the error.
        detail = (result.stderr or result.stdout or "").replace(key, "***").strip()
        raise RuntimeError(f"codex login failed: {detail[:300]}")


async def main() -> None:
    expose_llm_key()
    login_codex()
    identity = Identity()
    config = DevConfig()
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            model=config.model, approval_policy="never", custom_section=build_persona()
        )
    )
    agent = Agent.create(
        adapter=adapter,
        agent_id=identity.agent_id,
        api_key=identity.api_key,
        ws_url=identity.ws_url,
        rest_url=identity.rest_url,
    )
    await run_with_graceful_shutdown(agent)


if __name__ == "__main__":
    asyncio.run(main())
