# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk>=4.0.0"]
# ///
"""Provision (and tear down) the three demo agents on the Band platform.

Registers the PM, Developer, and Architect via the Human User API and writes two
artifacts next to this file:

  * ``agent_config.yaml`` — keyed config the conductor reads (id + key per role).
  * ``.demo/agents.env``  — shell-sourceable ids, keys, and names for launch.sh
                            (the real Band keys the host injects into each sandbox
                            via ``sbx secret set-custom``; gitignored).

Run with:
    uv run examples/docker_demo/provision.py          # create
    uv run examples/docker_demo/provision.py delete    # tear down
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from band_rest import AsyncRestClient
from band_rest.core.api_error import ApiError
from band_rest.types import (
    AgentRegisterRequest,
    BulkDeletionItemStatus,
    BulkDeletionJobStatus,
)
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import LogSettings

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "agent_config.yaml"
DEMO_DIR = HERE / ".demo"
AGENTS_ENV = DEMO_DIR / "agents.env"
AGENT_IDS = DEMO_DIR / "agent_ids.txt"
ROOM_IDS = DEMO_DIR / "room_ids.txt"
ROOM_DELETION_POLL_S = 1.0
ROOM_DELETION_TIMEOUT_S = 60.0
# The generated client declares these statuses as Literal aliases, with no enum
# to import; tests pin every value here to those aliases.
FINISHED_JOB_STATUSES: Final[frozenset[BulkDeletionJobStatus]] = frozenset(
    {"completed", "failed"}
)
DELETED_ITEM_STATUS: Final[BulkDeletionItemStatus] = "succeeded"


@dataclass(frozen=True)
class AgentSpec:
    config_key: str  # key in agent_config.yaml the conductor looks up
    env_prefix: str  # prefix in agents.env (DEMO_PM_ID, ...)
    name: str  # display name shown in the room
    description: str


# Every demo agent's description carries this marker so the sweep only ever
# deletes agents THIS demo created — never a user's real agent that happens to
# share a display name (Maya/Sam/Jordan).
DEMO_MARKER = "[band-demo]"

SPECS = [
    AgentSpec(
        "demo_pm", "DEMO_PM", "Maya (PM)", "Product manager & team lead (Claude SDK)"
    ),
    AgentSpec("demo_dev", "DEMO_DEV", "Sam (Dev)", "Lead developer (Codex)"),
    AgentSpec(
        "demo_architect",
        "DEMO_ARCHITECT",
        "Jordan (Architect)",
        "Software architect (CrewAI)",
    ),
]


class ProvisionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_api_key_user: str = ""
    band_rest_url: str = "https://app.band.ai"


def make_client(settings: ProvisionSettings) -> AsyncRestClient:
    if not settings.band_api_key_user:
        raise ValueError("BAND_API_KEY_USER is required to provision demo agents")
    return AsyncRestClient(
        api_key=settings.band_api_key_user, base_url=settings.band_rest_url
    )


def require_fresh_run() -> None:
    for artifact in (CONFIG_PATH, AGENTS_ENV, AGENT_IDS, ROOM_IDS):
        if artifact.exists():
            raise ValueError(
                f"{artifact} already exists; run launch.sh down before provisioning"
            )


async def create(client: AsyncRestClient) -> None:
    require_fresh_run()
    DEMO_DIR.mkdir(exist_ok=True)
    keyed_config: dict[str, dict[str, str]] = {}
    env_lines: list[str] = []

    try:
        for spec in SPECS:
            resp = await client.human_api_agents.register_my_agent(
                agent=AgentRegisterRequest(
                    name=spec.name, description=f"{DEMO_MARKER} {spec.description}"
                )
            )
            agent = resp.data.agent
            api_key = resp.data.credentials.api_key
            logger.info(
                "Registered %s (%s) id=%s", spec.name, spec.config_key, agent.id
            )

            # Persist the id as each agent is created so a mid-loop failure still
            # leaves a record for teardown (no leaked agents).
            with AGENT_IDS.open("a", encoding="utf-8") as ledger:
                ledger.write(f"{agent.id}\n")

            keyed_config[spec.config_key] = {"agent_id": agent.id, "api_key": api_key}
            # shlex.quote so a display name with spaces/parens ("Maya (PM)")
            # survives being sourced by launch.sh (`set -a; . agents.env`).
            env_lines += [
                f"{spec.env_prefix}_ID={shlex.quote(agent.id)}",
                f"{spec.env_prefix}_APIKEY={shlex.quote(api_key)}",
                f"{spec.env_prefix}_NAME={shlex.quote(agent.name)}",
            ]
    except Exception:
        logger.exception(
            "Provisioning failed; created ids remain in %s for cleanup", AGENT_IDS
        )
        raise

    CONFIG_PATH.write_text(
        yaml.dump(keyed_config, default_flow_style=False), encoding="utf-8"
    )
    AGENTS_ENV.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s, %s, %s", CONFIG_PATH.name, AGENTS_ENV, AGENT_IDS)


def record_room(room_id: str) -> None:
    """Append a created room to the teardown ledger before anything else can fail."""
    DEMO_DIR.mkdir(exist_ok=True)
    with ROOM_IDS.open("a", encoding="utf-8") as ledger:
        ledger.write(f"{room_id}\n")


async def delete_rooms(client: AsyncRestClient) -> None:
    """Delete every recorded room; one already gone counts as deleted."""
    if not ROOM_IDS.exists():
        return
    room_ids = ROOM_IDS.read_text(encoding="utf-8").split()
    deletions = client.human_api_bulk_deletions
    job = (await deletions.bulk_delete_my_chats(ids=room_ids)).data
    async with asyncio.timeout(ROOM_DELETION_TIMEOUT_S):
        while job.status not in FINISHED_JOB_STATUSES:
            await asyncio.sleep(ROOM_DELETION_POLL_S)
            job = (await deletions.show_my_bulk_deletion(job.id)).data
    results = {item.id: item.status for item in job.results}
    left = [
        room_id for room_id in room_ids if results.get(room_id) != DELETED_ITEM_STATUS
    ]
    if left:
        ROOM_IDS.write_text("\n".join(left) + "\n", encoding="utf-8")
        raise RuntimeError(f"Could not delete rooms {left}; rerun to retry")
    logger.info("Deleted rooms %s", ", ".join(room_ids))
    ROOM_IDS.unlink()


async def delete(client: AsyncRestClient) -> None:
    if not AGENT_IDS.exists():
        logger.info("No %s — nothing to delete", AGENT_IDS)
        return
    unresolved: list[str] = []
    for agent_id in AGENT_IDS.read_text(encoding="utf-8").split():
        try:
            await client.human_api_agents.delete_my_agent(agent_id, force=True)
        except ApiError as error:
            if error.status_code != 404:
                logger.exception("Could not delete agent %s", agent_id)
                unresolved.append(agent_id)
        except ValidationError:
            logger.exception("Invalid deletion response for agent %s", agent_id)
            unresolved.append(agent_id)
        except Exception:
            logger.exception("Could not delete agent %s", agent_id)
            unresolved.append(agent_id)
        else:
            logger.info("Deleted agent %s", agent_id)
    if unresolved:
        AGENT_IDS.write_text("\n".join(unresolved) + "\n", encoding="utf-8")
        raise RuntimeError(f"Cleanup incomplete; retry ids in {AGENT_IDS}")
    for artifact in (AGENT_IDS, AGENTS_ENV, CONFIG_PATH):
        artifact.unlink(missing_ok=True)


async def main() -> None:
    LogSettings().for_application().configure()
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        require_fresh_run()
        return
    settings = ProvisionSettings()
    client = make_client(settings)
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        try:
            await delete_rooms(client)
        finally:
            await delete(client)
    else:
        await create(client)


if __name__ == "__main__":
    asyncio.run(main())
