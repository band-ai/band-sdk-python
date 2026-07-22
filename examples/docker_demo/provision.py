# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
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
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from band_rest import AsyncRestClient
from band_rest.types import AgentRegisterRequest
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "agent_config.yaml"
DEMO_DIR = HERE / ".demo"
AGENTS_ENV = DEMO_DIR / "agents.env"
AGENT_IDS = DEMO_DIR / "agent_ids.txt"


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


async def sweep_stale(client: AsyncRestClient, spec: AgentSpec) -> None:
    """Delete a prior demo agent of this name whose teardown didn't fire.

    Scoped to demo-owned agents via DEMO_MARKER so a user's real agent that
    happens to share the display name is never touched.
    """
    existing = await client.human_api_agents.list_my_agents(name=spec.name)
    for old in existing.data:
        if old.name == spec.name and (old.description or "").startswith(DEMO_MARKER):
            await client.human_api_agents.delete_my_agent(old.id, force=True)
            logger.info("Removed stale demo agent %s (%s)", spec.name, old.id)


async def create(client: AsyncRestClient) -> None:
    DEMO_DIR.mkdir(exist_ok=True)
    keyed_config: dict[str, dict[str, str]] = {}
    env_lines: list[str] = []
    ids: list[str] = []

    try:
        for spec in SPECS:
            await sweep_stale(client, spec)
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

            ids.append(agent.id)
            # Persist the id as each agent is created so a mid-loop failure still
            # leaves a record for teardown (no leaked agents).
            AGENT_IDS.write_text("\n".join(ids) + "\n", encoding="utf-8")

            keyed_config[spec.config_key] = {"agent_id": agent.id, "api_key": api_key}
            env_lines += [
                f"{spec.env_prefix}_ID={agent.id}",
                f"{spec.env_prefix}_APIKEY={api_key}",
                f"{spec.env_prefix}_NAME={agent.name}",
            ]
    except Exception:
        # Roll back everything created so far so a partial failure leaks nothing.
        for agent_id in ids:
            await client.human_api_agents.delete_my_agent(agent_id, force=True)
        AGENT_IDS.unlink(missing_ok=True)
        logger.error("Provisioning failed; rolled back %d created agent(s)", len(ids))
        raise

    CONFIG_PATH.write_text(
        yaml.dump(keyed_config, default_flow_style=False), encoding="utf-8"
    )
    AGENTS_ENV.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s, %s, %s", CONFIG_PATH.name, AGENTS_ENV, AGENT_IDS)


async def delete(client: AsyncRestClient) -> None:
    if not AGENT_IDS.exists():
        logger.info("No %s — nothing to delete", AGENT_IDS)
        return
    for agent_id in AGENT_IDS.read_text(encoding="utf-8").split():
        await client.human_api_agents.delete_my_agent(agent_id, force=True)
        logger.info("Deleted agent %s", agent_id)
    for artifact in (AGENT_IDS, AGENTS_ENV, CONFIG_PATH):
        artifact.unlink(missing_ok=True)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [provision] %(message)s"
    )
    settings = ProvisionSettings()
    client = make_client(settings)
    if len(sys.argv) > 1 and sys.argv[1] == "delete":
        await delete(client)
    else:
        await create(client)


if __name__ == "__main__":
    asyncio.run(main())
