"""Resource and MCP-crash mechanics for the live crash-survival smokes.

Scenario steps and outcome asserts stay in the tests; this kit owns
provisioning, capture barriers, and the Letta-vs-Shared crash dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from band.integrations.letta.mcp import LettaMCPBridge
from band.integrations.mcp import BandMCPBackend, BandMCPTransport
from band.runtime.tools import BandTool
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe
from tests.e2e.baseline.timeouts import SlowTurnBudget, slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.mcpclient import crash_backend, served_tool_names

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=3)
LETTA_TIMEOUT_EXTRA = max(BUDGET.extra_s, 120)


@dataclass(frozen=True)
class LettaRegistration:
    """Stable Letta MCP registration identity captured before a crash."""

    server_id: str
    tool_ids: frozenset[str]

    @classmethod
    def capture(cls, adapter: Any) -> LettaRegistration:
        server_id = adapter._mcp.server_id
        tool_ids = frozenset(adapter._mcp.tool_ids)
        assert server_id, "adapter registered no MCP server with Letta"
        assert tool_ids, "Letta discovered no tools from the self-hosted MCP server"
        return cls(server_id=server_id, tool_ids=tool_ids)

    async def assert_repointed(self, adapter: Any) -> None:
        """Same registration ids, ready again, tools rediscovered via Letta."""
        assert adapter._mcp.ready
        assert adapter._mcp.server_id == self.server_id
        assert frozenset(adapter._mcp.tool_ids) == self.tool_ids
        discovered = await adapter._client.mcp_servers.tools.list(
            mcp_server_id=self.server_id
        )
        assert {
            tool.id for tool in discovered if getattr(tool, "id", None)
        } == self.tool_ids, (
            "Letta could not rediscover Band tools at the repointed URL"
        )

    def assert_stable(self, adapter: Any) -> None:
        """Registration identity survived a post-crash conversational turn."""
        assert adapter._mcp.server_id == self.server_id
        assert frozenset(adapter._mcp.tool_ids) == self.tool_ids
        assert adapter._mcp.ready


@dataclass(frozen=True)
class OpencodeRegistration:
    """OpenCode MCP registration captured before a crash."""

    name: str
    url: str
    backend: BandMCPBackend

    @classmethod
    def capture(cls, adapter: Any) -> OpencodeRegistration:
        registration = adapter._registration
        backend = adapter._mcp.current
        assert registration is not None, "OpenCode never registered Band MCP"
        assert backend is not None, "OpenCode has no Band MCP backend"
        return cls(
            name=adapter._mcp_server_name, url=registration.url, backend=backend
        )

    async def assert_reregistered(self, adapter: Any) -> None:
        """Same MCP name on a replacement backend that serves Band tools.

        Port reuse can keep the URL string identical; identity of the backend
        object is the durable replacement signal.
        """
        live = adapter._registration
        current = adapter._mcp.current
        assert live is not None, "OpenCode did not re-register Band MCP after the crash"
        assert adapter._mcp_server_name == self.name
        assert current is not None and current is not self.backend
        assert current.is_running
        served = await served_tool_names(
            live.url, BandMCPTransport.SSE, headers=live.headers
        )
        assert BandTool.SEND_MESSAGE in served

    def assert_replaced(self, adapter: Any) -> None:
        """Structural side signal after a conversational recovery turn."""
        live = adapter._registration
        current = adapter._mcp.current
        assert live is not None
        assert adapter._mcp_server_name == self.name
        assert current is not None and current is not self.backend
        assert current.is_running


@dataclass
class HostedAdapter:
    """A running cell adapter held for structural MCP recovery."""

    adapter: Any
    identity: ProvisionedAgent


@dataclass(frozen=True)
class LivenessTurn:
    """A settled liveness probe: reply window plus the trigger message id."""

    replies: Replies
    message_id: str


@dataclass
class HostedRoom:
    """A running cell adapter in a room with reply capture for turn barriers."""

    adapter: Any
    identity: ProvisionedAgent
    room_id: str
    capture: ReplyCapture
    user_ops: UserOps
    budget: SlowTurnBudget

    async def liveness_reply(self, marker: str) -> LivenessTurn:
        """Send a liveness probe; wait until PROCESSED and a reply are both in."""
        mark = self.capture.messages.snapshot()
        message_id = await self.user_ops.send_message(
            self.room_id,
            liveness_probe(marker),
            mention_id=self.identity.id,
            mention_name=self.identity.name,
        )
        replies = await self.capture.wait_for_reply(
            message_id,
            self.identity.id,
            since=mark,
            deadline_s=self.budget.deadline_s,
        )
        return LivenessTurn(replies=replies, message_id=message_id)


async def crash_band_mcp(adapter: Any) -> BandMCPBackend:
    """Crash the adapter's in-process Band MCP server the way a hang does."""
    owner = adapter._mcp
    backend = (
        await crash_backend(owner.backend)
        if isinstance(owner, LettaMCPBridge)
        else await crash_backend(owner)
    )
    assert isinstance(backend, BandMCPBackend)
    assert not backend.is_running
    return backend


def _build_adapter(
    cell: AdapterCell, *, config: Mapping[str, Any] | None = None
) -> Any:
    adapter = cell.build()
    if config:
        adapter.config = adapter.config.model_copy(update=dict(config))
    return adapter


@asynccontextmanager
async def hosted_adapter(
    cell: AdapterCell,
    *,
    label: str,
    config: Mapping[str, Any] | None = None,
) -> AsyncIterator[HostedAdapter]:
    """Provision, build, and run the cell's adapter without a room."""
    identity = await cell.provision(label=label)
    adapter = _build_adapter(cell, config=config)
    with cell.resources.track_running(identity.id):
        async with running_agent(identity, adapter, cell.settings):
            yield HostedAdapter(adapter=adapter, identity=identity)


@asynccontextmanager
async def hosted_room(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    *,
    label: str,
    title: str,
    config: Mapping[str, Any] | None = None,
    budget: SlowTurnBudget = BUDGET,
) -> AsyncIterator[HostedRoom]:
    """Provision a room and run the cell's adapter under reply capture."""
    identity = await cell.provision(label=label)
    adapter = _build_adapter(cell, config=config)
    room_id = await resource_manager.provision_room(
        title=title, participants=[identity.id]
    )
    with cell.resources.track_running(identity.id):
        async with (
            running_agent(identity, adapter, cell.settings),
            reply_capture(room_id) as capture,
        ):
            yield HostedRoom(
                adapter=adapter,
                identity=identity,
                room_id=room_id,
                capture=capture,
                user_ops=user_ops,
                budget=budget,
            )


async def ensure_opencode_mcp(adapter: Any) -> None:
    """Register (or re-register) Band MCP with OpenCode without a model turn."""
    await adapter._ensure_client_started()


async def ensure_letta_mcp(adapter: Any) -> None:
    """Repoint or re-register Letta's Band MCP path without a model turn."""
    await adapter._mcp.ensure_ready(adapter._client)
