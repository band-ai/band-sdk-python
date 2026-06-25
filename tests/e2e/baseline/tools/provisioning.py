"""Dynamic provisioning (mint/reap) for live E2E tests.

Mints fresh platform resources per run so tests never depend on a static,
pre-configured agent: register an agent (getting its own credentials), create
rooms, and force-delete everything on teardown. A prefix-guarded orphan sweep
reaps leftovers from crashed prior runs.

Minted agents are named ``e2e-band-{run_id}-{label}`` so the sweep can
recognise its own resources by prefix and never touch a non-test agent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from band_rest import AgentRegisterRequest, AsyncRestClient

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.tools.user_ops import UserOps

logger = logging.getLogger(__name__)

# All minted agent names start with this; the orphan sweep matches on it.
NAME_PREFIX = "e2e-band-"


def new_run_id() -> str:
    """Short token identifying a single test session's minted resources."""
    return uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class MintedAgent:
    """A freshly registered agent and its own credentials."""

    id: str
    api_key: str
    name: str


class ResourceManager:
    """Mints and reaps platform resources for one test run.

    Tracks everything it creates so ``reap_all`` can force-delete on teardown.
    Room operations are delegated to ``UserOps`` so the direct-REST delete path
    lives in exactly one place.
    """

    def __init__(
        self,
        *,
        user_client: AsyncRestClient,
        settings: BaselineSettings,
        run_id: str,
    ) -> None:
        self._client = user_client
        self._settings = settings
        self._run_id = run_id
        self._user_ops = UserOps(user_client)
        self._minted_agent_ids: list[str] = []
        self._minted_room_ids: list[str] = []

    @property
    def settings(self) -> BaselineSettings:
        return self._settings

    @property
    def client(self) -> AsyncRestClient:
        """The user-authenticated REST client, for platform state reads."""
        return self._client

    @property
    def user_ops(self) -> UserOps:
        return self._user_ops

    def _agent_name(self, label: str) -> str:
        return f"{NAME_PREFIX}{self._run_id}-{label}"

    async def mint_agent(
        self, label: str, *, description: str | None = None
    ) -> MintedAgent:
        """Register a fresh agent and return its id + own API key."""
        name = self._agent_name(label)
        response = await self._client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(
                name=name,
                description=description or f"E2E baseline test agent ({label})",
            )
        )
        agent = response.data.agent
        credentials = response.data.credentials
        assert agent is not None and agent.id, "register_my_agent returned no agent id"
        assert credentials is not None and credentials.api_key, (
            "register_my_agent returned no credentials"
        )
        self._minted_agent_ids.append(agent.id)
        logger.info("Minted agent %s (%s)", agent.id, name)
        return MintedAgent(id=agent.id, api_key=credentials.api_key, name=name)

    async def mint_room(
        self, *, title: str | None = None, participants: list[str] | None = None
    ) -> str:
        """Create a room as the user; optionally add participants. Returns id."""
        room_id = await self._user_ops.create_room(title=title)
        self._minted_room_ids.append(room_id)
        for participant_id in participants or []:
            await self._user_ops.add_participant(room_id, participant_id)
        logger.info("Minted room %s", room_id)
        return room_id

    async def reap_agent(self, agent_id: str) -> None:
        """Force-delete an agent."""
        await self._client.human_api_agents.delete_my_agent(agent_id, force=True)
        if agent_id in self._minted_agent_ids:
            self._minted_agent_ids.remove(agent_id)

    async def reap_room(self, room_id: str) -> None:
        await self._user_ops.delete_room(room_id)
        if room_id in self._minted_room_ids:
            self._minted_room_ids.remove(room_id)

    async def reap_all(self) -> None:
        """Best-effort teardown of everything minted this run.

        Logs ids before deleting so they stay recoverable from logs, and keeps
        going past individual failures (rooms first, then agents).
        """
        for room_id in list(self._minted_room_ids):
            logger.info("Reaping room %s", room_id)
            try:
                await self.reap_room(room_id)
            except Exception:
                logger.warning("Failed to reap room %s", room_id, exc_info=True)
        for agent_id in list(self._minted_agent_ids):
            logger.info("Reaping agent %s", agent_id)
            try:
                await self.reap_agent(agent_id)
            except Exception:
                logger.warning("Failed to reap agent %s", agent_id, exc_info=True)

    async def sweep_orphans(self) -> int:
        """Force-delete stale test agents left by crashed prior runs.

        Only touches agents whose name carries ``NAME_PREFIX``, belongs to a
        *different* run, and is older than ``orphan_max_age_minutes`` — so a
        concurrent run on the same shared platform is never deleted mid-flight.
        Returns the number of agents reaped.
        """
        max_age = timedelta(minutes=self._settings.run.orphan_max_age_minutes)
        cutoff = datetime.now(timezone.utc) - max_age
        response = await self._client.human_api_agents.list_my_agents(
            name=NAME_PREFIX, page_size=100
        )
        reaped = 0
        for agent in response.data:
            if not agent.name.startswith(NAME_PREFIX):
                continue  # name filter is a contains-match; re-check the prefix
            if f"-{self._run_id}-" in agent.name:
                continue  # never reap our own run
            if agent.inserted_at > cutoff:
                continue  # too fresh — could be a concurrent run
            logger.info("Sweeping orphan agent %s (%s)", agent.id, agent.name)
            try:
                await self._client.human_api_agents.delete_my_agent(
                    agent.id, force=True
                )
                reaped += 1
            except Exception:
                logger.warning(
                    "Failed to sweep orphan agent %s", agent.id, exc_info=True
                )
        if reaped:
            logger.info("Orphan sweep reaped %d agent(s)", reaped)
        return reaped


@asynccontextmanager
async def running_minted_agent(
    adapter: SimpleAdapter[Any],
    resources: ResourceManager,
    *,
    label: str = "aut",
) -> AsyncGenerator[tuple[Agent, MintedAgent], None]:
    """Mint an agent and run ``adapter`` as it for the duration of the block.

    Yields ``(agent, creds)`` with the agent connected. Reaping is owned by the
    resource manager's teardown (the minted agent is tracked at mint time), so
    this only manages the running agent's own lifecycle.
    """
    creds = await resources.mint_agent(label)
    endpoints = resources.settings.endpoints
    agent = Agent.create(
        adapter=adapter,
        agent_id=creds.id,
        api_key=creds.api_key,
        ws_url=endpoints.ws_url,
        rest_url=endpoints.rest_url,
    )
    async with agent:
        yield agent, creds
