"""Dynamic mint/reap of companion agents for the live baseline E2E lanes.

Enabled by ``E2E_DYNAMIC_AGENTS=true``. Uses the Enterprise Human API
(``register_my_agent`` / ``delete_my_agent``) so the live lanes need no
pre-provisioned companion-agent secrets. Provisioned from ``pytest_configure``
(before collection, so the module-level baseline settings observe the populated
env) and reaped in ``pytest_unconfigure``.

When the flag is unset this module is never imported by the conftest hook, so
there is zero behavior change for ordinary runs.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

from band_rest import AgentRegisterRequest, AsyncRestClient

logger = logging.getLogger(__name__)

# All minted agents carry this name prefix so a startup sweep can reap agents
# left behind by a crashed prior run without touching real agents.
AGENT_NAME_PREFIX = "e2e-"


@dataclass(frozen=True)
class MintedAgent:
    agent_id: str
    api_key: str
    name: str
    handle: str  # platform handle, without a leading "@"


# (label, env_prefix, mint_description, set_handle, set_description)
# label -> minted agent name "e2e-<label>-<run_id>"; env_prefix is the settings
# group prefix the scenarios read.
_L3_TEST_DESC = (
    "Routes requests using participant descriptions and relays final answers."
)
_L3_CALC_DESC = "Performs exact arithmetic and returns only computed results."
_L3_GREETER_DESC = "Crafts personalized greetings and greeting-card copy."

_COMPANIONS: tuple[tuple[str, str, str, bool, bool], ...] = (
    ("echo", "E2E_ECHO_AGENT_", "Deterministic echo companion agent", True, False),
    ("l3-test", "E2E_L3_TEST_AGENT_", _L3_TEST_DESC, True, True),
    ("l3-calc", "E2E_L3_CALC_AGENT_", _L3_CALC_DESC, True, True),
    ("l3-greeter", "E2E_L3_GREETER_AGENT_", _L3_GREETER_DESC, True, True),
)

# L4 per-framework agents are read via _adapter_credentials_from_env, whose
# prefix is "E2E_<ADAPTER>_AGENT_" (adapter upper-cased, '-' -> '_').
_L4_FRAMEWORKS: tuple[str, ...] = (
    "langgraph",
    "anthropic",
    "pydantic_ai",
    "claude_sdk",
    "gemini",
)


def _l4_env_prefix(adapter: str) -> str:
    return "E2E_" + adapter.upper().replace("-", "_") + "_AGENT_"


@dataclass
class DynamicProvisioner:
    """Mints/reaps companion agents via the Enterprise Human API."""

    user_api_key: str
    base_url: str
    run_id: str
    _user_client: AsyncRestClient = field(init=False, repr=False)
    _minted_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._user_client = AsyncRestClient(
            api_key=self.user_api_key, base_url=self.base_url
        )

    async def mint_agent(self, label: str, description: str) -> MintedAgent:
        name = f"{AGENT_NAME_PREFIX}{label}-{self.run_id}"
        resp = await self._user_client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(name=name, description=description),
        )
        agent = resp.data.agent
        api_key = resp.data.credentials.api_key
        if not api_key:
            raise RuntimeError(f"register_my_agent returned no api_key for {name!r}")
        self._minted_ids.append(agent.id)
        # The register response carries no handle; read the real one by
        # authenticating as the freshly-minted agent.
        agent_client = AsyncRestClient(api_key=api_key, base_url=self.base_url)
        me = await agent_client.agent_api_identity.get_agent_me()
        handle = (getattr(me.data, "handle", "") or "").lstrip("@")
        logger.info("minted agent %s (%s) handle=%s", name, agent.id, handle)
        return MintedAgent(
            agent_id=agent.id,
            api_key=api_key,
            name=agent.name or name,
            handle=handle,
        )

    async def sweep_orphans(self) -> None:
        """Delete leftover e2e-* agents from a crashed prior run."""
        resp = await self._user_client.human_api_agents.list_my_agents(page_size=100)
        for agent in resp.data or []:
            if (getattr(agent, "name", "") or "").startswith(AGENT_NAME_PREFIX):
                try:
                    await self._user_client.human_api_agents.delete_my_agent(
                        id=agent.id
                    )
                    logger.info("swept orphan agent %s", agent.id)
                except Exception:
                    logger.warning("sweep failed for %s", agent.id, exc_info=True)

    async def teardown(self) -> None:
        for agent_id in list(self._minted_ids):
            try:
                await self._user_client.human_api_agents.delete_my_agent(id=agent_id)
            except Exception:
                logger.warning("teardown delete failed for %s", agent_id, exc_info=True)
        self._minted_ids.clear()


def _set_agent_env(
    prefix: str,
    agent: MintedAgent,
    *,
    set_handle: bool,
    description: str | None,
) -> None:
    os.environ[f"{prefix}ID"] = agent.agent_id
    os.environ[f"{prefix}API_KEY"] = agent.api_key
    os.environ[f"{prefix}NAME"] = agent.name
    if set_handle:
        os.environ[f"{prefix}HANDLE"] = f"@{agent.handle}" if agent.handle else ""
    if description is not None:
        os.environ[f"{prefix}DESCRIPTION"] = description


async def provision(provisioner: DynamicProvisioner) -> None:
    """Sweep orphans, mint the companion set, and populate the env the
    baseline scenarios read."""
    await provisioner.sweep_orphans()

    for label, prefix, description, set_handle, set_description in _COMPANIONS:
        agent = await provisioner.mint_agent(label, description)
        _set_agent_env(
            prefix,
            agent,
            set_handle=set_handle,
            description=description if set_description else None,
        )

    for framework in _L4_FRAMEWORKS:
        agent = await provisioner.mint_agent(
            f"l4-{framework}", f"L4 rehydration agent ({framework})"
        )
        _set_agent_env(
            _l4_env_prefix(framework),
            agent,
            set_handle=False,
            description=None,
        )


def new_run_id() -> str:
    return os.environ.get("E2E_BASELINE_RUN_ID") or uuid.uuid4().hex[:8]
