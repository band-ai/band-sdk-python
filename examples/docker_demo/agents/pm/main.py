"""PM / team-lead agent (Claude SDK) for the Band Docker demo.

The kit launcher execs this file with the workspace's own locked venv; Band
identity and endpoints arrive as BAND_* env vars, and under proxy-managed
custody ``BAND_API_KEY`` is only the sentinel — the real key is injected on the
wire by the sandbox proxy and never enters this VM.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import AdapterFeatures, Capability, Emit
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


class PMConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PM_", case_sensitive=False, env_ignore_empty=True
    )

    model: str | None = None
    # How the PM addresses the architect when handing off. Matches the fixed name
    # provision.py registers (Jordan), so band_lookup_peers finds it.
    architect_name: str = "Jordan (Architect)"


def build_persona(architect_name: str) -> str:
    persona = (
        (Path(__file__).parent / "prompt.md")
        .read_text(encoding="utf-8")
        .format(architect_name=architect_name)
    )
    return f"{persona}\n\n{CONVERSATION_DISCIPLINE}"


def expose_llm_key() -> None:
    """Copy the sbx-injected placeholder into the var the claude CLI reads.

    sbx reserves ANTHROPIC_API_KEY for its wire-only injection (empty in the VM),
    so the launcher injects the placeholder under ANTHROPIC_PROXY_KEY; the real
    key stays on the host and the proxy swaps the placeholder on the wire.
    """
    if proxy := os.environ.get("ANTHROPIC_PROXY_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = proxy


async def main() -> None:
    # INFO so the Band lifecycle trace (messages, tool calls, replies) shows in the
    # sandbox log the demo pane tails — without this, only WARNING+ would surface.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    expose_llm_key()
    identity = Identity()
    config = PMConfig()
    adapter = ClaudeSDKAdapter(
        model=config.model,
        custom_section=build_persona(config.architect_name),
        # Surface the meeting's mechanics live in the room: tool_call/tool_result
        # for every Band tool (the peer lookup + add-participant handoff), the
        # agent's reasoning, and memory tools so decisions can be recorded.
        features=AdapterFeatures(
            emit={Emit.EXECUTION, Emit.THOUGHTS},
            capabilities={Capability.MEMORY},
        ),
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
