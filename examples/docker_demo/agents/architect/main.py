"""Software Architect agent (CrewAI) for the Band Docker demo.

The kit launcher execs this file with the workspace's own locked venv; Band
identity and endpoints arrive as BAND_* env vars. The OpenAI credential is
provided host-side (``sbx secret set -g openai``) so it never enters this VM.
The architect stays silent until the PM @mentions it for a decision.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters.crewai import CrewAIAdapter
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


class ArchitectConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARCHITECT_", case_sensitive=False, env_ignore_empty=True
    )

    model: str = "gpt-5.4"


def build_persona() -> str:
    persona = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")
    return f"{persona}\n\n{CONVERSATION_DISCIPLINE}"


def expose_llm_key() -> None:
    """Copy the sbx-injected placeholder into OPENAI_API_KEY for litellm/crewai.

    sbx reserves OPENAI_API_KEY for its wire-only injection (empty in the VM), so
    the launcher injects the placeholder under OPENAI_PROXY_KEY; the real key stays
    on the host and the proxy swaps the placeholder on the wire.
    """
    if proxy := os.environ.get("OPENAI_PROXY_KEY"):
        os.environ["OPENAI_API_KEY"] = proxy


async def main() -> None:
    expose_llm_key()
    identity = Identity()
    config = ArchitectConfig()
    adapter = CrewAIAdapter(model=config.model, custom_section=build_persona())
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
