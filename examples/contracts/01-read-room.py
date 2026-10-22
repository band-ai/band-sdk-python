# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[contracts]>=6.0.1"]
# ///
"""Read one authorized contract room without logging its private content."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from band import configure_logging
from band.config import PlatformSettings, load_agent_config
from band.contracts import ContractClient, ContractConnection

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class Settings(PlatformSettings):
    """Load platform and room settings from the canonical example configuration."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env.test",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    agent_key: str = "contract_agent"
    contract_room: str = Field(min_length=1)
    contract_session: str = Field(min_length=1)


async def main() -> None:
    settings = Settings()
    agent_id, api_key = load_agent_config(
        settings.agent_key, config_path=ROOT / "agent_config.yaml"
    )
    connection = ContractConnection(
        url=settings.BAND_REST_URL, credential=SecretStr(api_key)
    )
    async with ContractClient(connection) as client:
        room = await client.room(
            settings.contract_room,
            settings.contract_session,
            actor=f"Agent:{agent_id}",
        )
        await room.view()
        page = await room.inbox(after=0, limit=10)
        logger.info(
            "Verified room identity and view; read %s events through cursor %s",
            len(page.events),
            page.cursor,
        )


if __name__ == "__main__":
    configure_logging(logging.INFO)
    asyncio.run(main())
