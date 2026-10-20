# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[pydantic-ai]>=6.0.0"]
# ///
"""Two draft tools share a temporary workspace deleted when the host stops.

Run: uv run examples/pydantic_ai/draft-workspace.py
Configure pydantic_agent in root agent_config.yaml and keys in root .env.test.
The draft is shared across this adapter's rooms and discarded on shutdown.
Build a fresh workspace, adapter and host for each new lifetime.
Try: "Save a draft announcing our Tuesday release", then "Read the draft".
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.config.loader import load_agent_config
from band.core.defaultmodels import OPENAI_MODEL
from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    DRAFT_AGENT: str = "pydantic_agent"
    DRAFT_CONFIG: Path = PROJECT_ROOT / "agent_config.yaml"


class SaveDraftInput(BaseModel):
    """Replace the team's temporary draft."""

    text: str = Field(min_length=1, max_length=16000, description="Complete draft text")


class ReadDraftInput(BaseModel):
    """Read the team's current temporary draft."""


def shared_tools(workspace: Path) -> list[CustomToolDef]:
    """Use a fixed filename so tool input cannot escape the workspace."""
    draft = workspace / "draft.txt"

    async def save_draft(args: SaveDraftInput) -> str:
        draft.write_text(args.text, encoding="utf-8")
        return "Draft saved."

    async def read_draft(args: ReadDraftInput) -> str:
        try:
            return draft.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "No draft yet."

    return [(SaveDraftInput, save_draft), (ReadDraftInput, read_draft)]


def build_adapter(workspace: TemporaryDirectory[str]) -> PydanticAIAdapter:
    """Register async cleanup for the adapter's synchronous resource."""

    async def remove_workspace() -> None:
        workspace.cleanup()

    adapter = PydanticAIAdapter(
        PydanticAIAdapterConfig(
            model=f"openai:{OPENAI_MODEL}",
            custom_section="Save and read the team's temporary draft.",
        ),
        additional_tools=shared_tools(Path(workspace.name)),
    )
    adapter.add_cleanup(remove_workspace)
    return adapter


async def build_agent(settings: Settings) -> Agent:
    """Release construction failures locally; the completed host owns cleanup."""
    load_agent_config(settings.DRAFT_AGENT, config_path=settings.DRAFT_CONFIG)
    async with AsyncExitStack() as pending:
        workspace = TemporaryDirectory(prefix="band-draft-")
        pending.enter_context(workspace)
        agent = Agent.from_config(
            settings.DRAFT_AGENT,
            config_path=settings.DRAFT_CONFIG,
            adapter=build_adapter(workspace),
        )
        pending.pop_all()
        return agent


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env.test", override=True)
    configure_logging(logging.INFO)
    agent = await build_agent(Settings())
    logger.info("Draft tools are ready; the draft is discarded at shutdown")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
