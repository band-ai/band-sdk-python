"""Capability-filtered matrix smokes — demonstrate ``@per_adapter`` filtering.

Two complementary scenarios driven entirely by capability filters, with no
hard-coded adapter lists:

* ``supports={Capability.MEMORY}`` selects the memory-capable adapters and runs a
  store-then-read-back memory scenario (``features=memory_features()`` exposes the
  memory tools per cell);
* ``without={Capability.MEMORY}`` selects the exact complement (the non-memory
  adapters) and runs a basic reply turn.

The two sets partition the matrix, so adding/removing an adapter or flipping its
``supports`` in the registry re-balances both tests automatically — the point of
the demonstration. Under fail-never-skip a cell whose backend/key is absent ERRORs
with the reason (e.g. ``GOOGLE_API_KEY`` for gemini, ``OPENCODE_BASE_URL`` for
opencode); that is the honest "not wired up" signal, not a regression.
"""

from __future__ import annotations

import pytest

from band.core.memory_types import MemoryListScope
from band.core.types import Capability

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    MEMORY_AGENT,
    recall_memory_instruction,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(supports={Capability.MEMORY}, **MEMORY_AGENT)
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
@pytest.mark.asyncio(loop_scope="session")
async def test_store_memory_across_memory_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Every memory-capable adapter can store a memory that lands in the store.

    The matrix here is *exactly* the adapters advertising ``Capability.MEMORY`` —
    selected by the filter, not a list — each built with the memory tools enabled.
    """
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
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=120)  # store -> list -> get is a multi-tool turn
@pytest.mark.asyncio(loop_scope="session")
async def test_recall_memory_across_memory_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Every memory-capable adapter can store a memory and read it back.

    The complement of ``test_store_memory_across_memory_adapters`` (which proves the
    *store* lands): the same subgroup drives a store -> list -> get sequence in one
    turn, so the assertion covers the memory tools' *read* path too. The record must
    land AND the agent must both query (``assert_list_called``) and fetch it back by
    id (``assert_get_called``) — list alone would pass on a mis-wired read that
    returns nothing, so the get hop is what proves an actual read-back.
    """
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

    # Write side: the record landed in the store.
    mem.stored.assert_stored(content=marker)
    # Read side: the agent queried its memory and fetched the record back by id.
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()


@per_adapter(without={Capability.MEMORY})
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
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
        await capture.wait_for_processed(trigger, agent.id)

    capture.messages.assert_present(what=f"a reply from {agent.adapter_id}")
