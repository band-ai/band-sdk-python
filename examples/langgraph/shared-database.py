# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph] @ git+ssh://git@github.com/band-ai/band-sdk-python.git@807b075342e3d1b0031a859115bbbbe9c77d8538", "aiosqlite>=0.21.0"]
# ///
"""Save and list team notes through one adapter-owned SQLite connection.

Run: uv run examples/langgraph/shared-database.py
Configure langgraph_agent in root agent_config.yaml and keys in root .env.test.
NOTES_DATABASE defaults to notes.sqlite3 in the working directory; notes persist.
All rooms using this adapter share these notes. Rebuild the adapter after stop.
Try: "Remember that our release day is Tuesday", then "List the team notes".
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from pathlib import Path

import aiosqlite
from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters.langgraph import LangGraphAdapter, LangGraphAdapterConfig
from band.config.loader import load_agent_config
from band.core.defaultmodels import OPENAI_MODEL
from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    NOTES_AGENT: str = "langgraph_agent"
    NOTES_CONFIG: Path = PROJECT_ROOT / "agent_config.yaml"
    NOTES_DATABASE: Path = Path("notes.sqlite3")


class SaveNoteInput(BaseModel):
    """Save a team note, shared across this adapter's rooms."""

    text: str = Field(min_length=1, max_length=4000, description="Note to remember")


class ListNotesInput(BaseModel):
    """List the twenty most recent team notes."""


def shared_tools(database: aiosqlite.Connection) -> list[CustomToolDef]:
    """Both handlers use the same connection, never a connection per call."""

    async def save_note(args: SaveNoteInput) -> str:
        async with database.execute(
            "INSERT INTO notes (text) VALUES (?)", (args.text,)
        ):
            pass
        return "Note saved."

    async def list_notes(args: ListNotesInput) -> str:
        async with database.execute(
            "SELECT text FROM notes ORDER BY id DESC LIMIT 20"
        ) as cursor:
            return json.dumps([row[0] for row in await cursor.fetchall()])

    return [(SaveNoteInput, save_note), (ListNotesInput, list_notes)]


def build_adapter(
    database: aiosqlite.Connection, *, llm: BaseChatModel | None = None
) -> LangGraphAdapter:
    """Register connection cleanup before constructing its host."""
    adapter = LangGraphAdapter(
        LangGraphAdapterConfig(
            custom_section="Use save_note and list_notes for the team's shared notebook."
        ),
        llm=llm if llm is not None else ChatOpenAI(model=OPENAI_MODEL),
        additional_tools=shared_tools(database),
    )
    adapter.add_cleanup(database.close)
    return adapter


async def build_agent(settings: Settings) -> Agent:
    """Close construction failures locally; hand off only a complete host."""
    load_agent_config(settings.NOTES_AGENT, config_path=settings.NOTES_CONFIG)
    async with AsyncExitStack() as pending:
        database = await pending.enter_async_context(
            aiosqlite.connect(settings.NOTES_DATABASE, isolation_level=None)
        )
        async with database.execute(
            "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL)"
        ):
            pass
        agent = Agent.from_config(
            settings.NOTES_AGENT,
            config_path=settings.NOTES_CONFIG,
            adapter=build_adapter(database),
        )
        pending.pop_all()
        return agent


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env.test", override=True)
    configure_logging(logging.INFO)
    settings = Settings()
    agent = await build_agent(settings)
    logger.info("Team notes persist in %s", settings.NOTES_DATABASE.resolve())
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
