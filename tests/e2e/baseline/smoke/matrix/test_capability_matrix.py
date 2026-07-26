"""Grid tests for adapters that expose memory or contacts capabilities."""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from band.core.memory_types import MemoryListScope
from band.core.types import Capability

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    CONTACTS_AGENT,
    MEMORY_AGENT,
    list_contacts_instruction,
    recall_memory_instruction,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.observations import ContactTool
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(supports={Capability.MEMORY}, **MEMORY_AGENT)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.asyncio(loop_scope="session")
async def test_store_memory_across_memory_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store an organization-scoped memory through each memory-capable adapter."""
    marker = unique_marker("xmem")
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-memory-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            store_memory_instruction(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        mem = await capture.memory(
            agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
        )

    mem.stored.assert_stored(content=marker)


@per_adapter(supports={Capability.MEMORY}, **MEMORY_AGENT)
@flaky_infra("only transient failures")
@pytest.mark.timeout(extra=120)  # store -> list -> get is a multi-tool turn
@pytest.mark.asyncio(loop_scope="session")
async def test_recall_memory_across_memory_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store, list, and fetch a memory through each memory-capable adapter."""
    marker = unique_marker("rmem")
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-recall-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            recall_memory_instruction(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        mem = await capture.memory(
            agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
        )

    mem.stored.assert_stored(content=marker)
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()


@per_adapter(supports={Capability.CONTACTS}, **CONTACTS_AGENT)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.asyncio(loop_scope="session")
async def test_list_contacts_across_contacts_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Every contacts-capable adapter can list contacts through the platform."""
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-contacts-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            list_contacts_instruction(),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(ContactTool.LIST.value)


@per_adapter(without={Capability.MEMORY})
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
@pytest.mark.asyncio(loop_scope="session")
async def test_reply_across_non_memory_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The complement — adapters that do not advertise memory — still handle a turn.

    Same filter mechanism, inverted: ``without={Capability.MEMORY}`` yields exactly
    the adapters the memory test does not, with no overlap and no hard-coded ids.
    """
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-nomemory-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        trigger = await user_ops.send_message(
            room_id,
            "Please reply with a short greeting.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(trigger, agent.id)

    replies.assert_present(what=f"a reply from {agent.adapter_id}")
