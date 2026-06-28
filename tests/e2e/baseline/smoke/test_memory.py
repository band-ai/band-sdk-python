"""Memory smokes: drive deterministic memory operations and assert at both layers
from one ``capture.memory(agent)`` read -- the *call* layer
(``mem.calls.assert_store_called`` etc.) and the *store* layer
(``mem.stored.assert_stored`` / ``where``).

Memories carry a unique marker so the reads are collision-free; agents run with
``Emit.EXECUTION`` so the calls surface as ``tool_call`` events.

Precondition: memory tools are an enterprise opt-in -- without the entitlement the
tools error and the store-layer assertions fail.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.memory_types import (
    MemoryListScope,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    WorkingLongTermMemoryType,
)

from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.sample_agents import (
    adapter_params,
    archive_memory_instruction,
    build_agent,
    memory_features,
    recall_memory_instruction,
    store_memory_instruction,
    store_subject_memory_instruction,
    store_two_memories_instruction,
    supersede_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.observations import MemoryTool
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.user_ops import UserOps

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]


# Anthropic-only: gpt-5.4-mini (LangGraph) intermittently skips band_store_memory
# (same flakiness as the event matrix; prompt/few-shot didn't fix it). The store
# reader is adapter-agnostic, so one reliable driver suffices.
@pytest.mark.parametrize("adapter_id", adapter_params(include={"anthropic"}))
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_stored(
    adapter_id: str,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """On each adapter: the store tool fired (call layer) and an org-scoped memory
    landed in the store (store layer), both carrying our marker."""
    marker = unique_marker("mem")
    adapter = build_agent(adapter_id, baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label=adapter_id
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                store_memory_instruction(marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            # One read, both layers (call layer from room events, store layer from
            # the agent's own memories filtered to our marker).
            mem = await capture.memory(
                agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
            )

    mem.calls.assert_store_called(
        content=marker,
        scope=MemoryStoreScope.ORGANIZATION,
        system=MemorySystem.LONG_TERM,
        type=WorkingLongTermMemoryType.SEMANTIC,
    )
    mem.stored.assert_stored(
        content=marker,
        scope=MemoryStoreScope.ORGANIZATION,
        system=MemorySystem.LONG_TERM,
        type=WorkingLongTermMemoryType.SEMANTIC,
    )


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_subject_scope(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A subject-scoped store about the agent itself, read back by subject_id.

    Exercises ``MemoryStoreScope.SUBJECT`` end to end plus ``where(subject_id=...)``
    filtering; the agent's own id is the subject, passed in the instruction.
    """
    marker = unique_marker("subjmem")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label="memsubj"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-subject", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                store_subject_memory_instruction(marker, subject_id=agent.id),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            mem = await capture.memory(
                agent,
                scope=MemoryListScope.SUBJECT,
                subject_id=agent.id,
                content_query=marker,
            )

    mem.calls.assert_store_called(
        content=marker,
        scope=MemoryStoreScope.SUBJECT,
        subject_id=agent.id,
    )
    mem.stored.assert_stored(content=marker, scope=MemoryStoreScope.SUBJECT)
    mem.stored.where(subject_id=agent.id).assert_present()


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_excluded_from_general_tool_view(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Memory tool calls are opted out of the general ``tool_calls()`` view by
    default, but reachable via ``include_memory=True``, ``named()``, and ``memory()``."""
    marker = unique_marker("mem")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label="memfilter"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-filter", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                store_memory_instruction(marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            general = await capture.tool_calls(sender_id=agent.id)
            with_memory = await capture.tool_calls(
                sender_id=agent.id, include_memory=True
            )
            mem = await capture.memory(
                agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
            )

    # Excluded from the general view by default...
    assert not general.fired(MemoryTool.STORE), (
        f"memory tool leaked into the general view: {[c.name for c in general]}"
    )
    # ...but present when opted in, via the named() subset, and via memory().
    with_memory.assert_fired(MemoryTool.STORE)
    with_memory.named(MemoryTool.STORE).assert_fired(MemoryTool.STORE)
    mem.calls.assert_store_called(content=marker)


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_lifecycle_supersede(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then supersede in one turn: both ops fire and the record ends up
    superseded, demonstrating the lifecycle tools and the ``status`` dimension."""
    marker = unique_marker("lifemem")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label="memlife"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-life", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                supersede_memory_instruction(marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            # status=ALL so the now-superseded record is still returned.
            mem = await capture.memory(
                agent,
                scope=MemoryListScope.ORGANIZATION,
                content_query=marker,
                status=MemoryStatus.ALL,
            )

    # Call layer: both lifecycle operations fired.
    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_supersede_called()
    # Store layer: the record is now superseded, not active.
    mem.stored.where(status=MemoryStatus.SUPERSEDED).assert_present()
    mem.stored.where(status=MemoryStatus.ACTIVE).assert_none()


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_lifecycle_archive(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then archive in one turn: the record ends up archived, not active."""
    marker = unique_marker("arcmem")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(adapter, resource_manager, label="memarc") as (
        _,
        agent,
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-archive", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                archive_memory_instruction(marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            mem = await capture.memory(
                agent,
                scope=MemoryListScope.ORGANIZATION,
                content_query=marker,
                status=MemoryStatus.ALL,
            )

    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_archive_called()
    mem.stored.where(status=MemoryStatus.ARCHIVED).assert_present()
    mem.stored.where(status=MemoryStatus.ACTIVE).assert_none()


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_recall(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then recall: the read-side list and get tools fire (call layer)."""
    marker = unique_marker("recall")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label="memrecall"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-recall", participants=[agent.id]
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

    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()
    mem.stored.assert_stored(content=marker)


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_store_layer_filtering(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two memories sharing a marker but differing in system/type: one read,
    sliced by dimension with where()."""
    marker = unique_marker("multi")
    adapter = build_agent("anthropic", baseline_settings, features=memory_features())
    async with running_provisioned_agent(
        adapter, resource_manager, label="memfilter2"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-memory-filtering", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                store_two_memories_instruction(marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            mem = await capture.memory(
                agent, scope=MemoryListScope.ORGANIZATION, content_query=marker
            )

    # Both landed; slice the single collection by dimension.
    mem.stored.assert_at_least(2)
    mem.stored.where(system=MemorySystem.LONG_TERM).assert_stored(
        content=marker, type=WorkingLongTermMemoryType.SEMANTIC
    )
    mem.stored.where(system=MemorySystem.WORKING).assert_stored(
        content=marker, type=WorkingLongTermMemoryType.EPISODIC
    )


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_visible_cross_agent_cross_room(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """An organization-scoped memory stored by one agent in one room is visible to
    a different agent reading from a different room -- memory is org-scoped, not
    room-scoped, and org memories are shared across agents in the same org."""
    marker = unique_marker("xorg")
    writer = build_agent("anthropic", baseline_settings, features=memory_features())
    reader = build_agent("anthropic", baseline_settings, features=memory_features())
    async with (
        running_provisioned_agent(writer, resource_manager, label="memwriter") as (
            _,
            agent_w,
        ),
        running_provisioned_agent(reader, resource_manager, label="memreader") as (
            _,
            agent_r,
        ),
    ):
        room_w = await resource_manager.provision_room(
            title="e2e-memory-xroom-writer", participants=[agent_w.id]
        )
        async with reply_capture(room_w) as cap_w:
            mid = await user_ops.send_message(
                room_w,
                store_memory_instruction(marker),
                mention_id=agent_w.id,
                mention_name=agent_w.name,
            )
            await cap_w.wait_for_processed(mid, agent_w.id)
            mem_w = await cap_w.memory(
                agent_w, scope=MemoryListScope.ORGANIZATION, content_query=marker
            )

        # Different agent, different room: read the store through the reader's own
        # client. No turn is needed -- the writer's store is already durable.
        room_r = await resource_manager.provision_room(
            title="e2e-memory-xroom-reader", participants=[agent_r.id]
        )
        async with reply_capture(room_r) as cap_r:
            mem_r = await cap_r.memory(
                agent_r, scope=MemoryListScope.ORGANIZATION, content_query=marker
            )

    # Writer stored it (both layers).
    mem_w.calls.assert_store_called(content=marker)
    mem_w.stored.assert_stored(content=marker)
    # Reader, in a different room, sees the same org memory without having written
    # anything itself.
    mem_r.stored.assert_stored(content=marker)
    assert not mem_r.calls, "reader should not have called any memory tool"
