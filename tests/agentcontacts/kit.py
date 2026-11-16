"""Minimal real adapter and agent for contact wiring tests."""

from __future__ import annotations

from typing import Any

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter
from band.runtime.agentconfig import AgentConfig
from band.runtime.contacts import ContactEventConfig
from tests.fakeplatform import REST_URL
from tests.identifiers import AGENT_ID


class ContactAdapter(SimpleAdapter[Any]):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        return None


def contact_agent(ws_url: str, contact_config: ContactEventConfig) -> Agent:
    """An agent on the local platform; xdist workers share its id, not its lock."""
    return Agent.create(
        adapter=ContactAdapter(),
        agent_id=AGENT_ID,
        api_key="test-key",
        ws_url=ws_url,
        rest_url=REST_URL,
        config=AgentConfig(single_instance=False),
        contact_config=contact_config,
    )
