"""Capability-filtered matrix smokes — demonstrate ``@across_adapters`` filtering.

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

from tests.e2e.baseline.agents import across_adapters
from tests.e2e.baseline.smoke.samples.sample_agents import (
    MEMORY_AGENT,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.assertions import assert_present
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@across_adapters(supports={Capability.MEMORY}, **MEMORY_AGENT)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_store_memory_across_memory_adapters(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
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
        title=f"e2e-cap-memory-{adapter_id}", participants=[matrix_agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            store_memory_instruction(marker),
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(mid, matrix_agent.id)
        mem = await capture.memory(
            matrix_agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
        )

    mem.stored.assert_stored(content=marker)


@across_adapters(without={Capability.MEMORY})
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_reply_across_non_memory_adapters(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The complement — adapters that do not advertise memory — still handle a turn.

    Same filter mechanism, inverted: ``without={Capability.MEMORY}`` yields exactly
    the adapters the memory test does not, with no overlap and no hard-coded ids.
    """
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-nomemory-{adapter_id}", participants=[matrix_agent.id]
    )
    async with reply_capture(room_id) as capture:
        trigger = await user_ops.send_message(
            room_id,
            "Please reply with a short greeting.",
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(trigger, matrix_agent.id)

    assert_present(capture.messages, what=f"a reply from {adapter_id}")
