"""Memory smokes: drive deterministic memory operations and assert at both layers
from one ``capture.memory(agent)`` read -- the *call* layer
(``mem.calls.assert_store_called`` etc.) and the *store* layer
(``mem.stored.assert_stored`` / ``where``).

Memories carry a unique marker so the reads are collision-free; agents run with
``Emit.TOOL_CALLS`` (via ``memory_features()``) so the calls surface as ``tool_call``
events, under the exact-execution prompt so the only action is the requested op.

Anthropic-only: gpt-5.4-mini (LangGraph) intermittently skips band_store_memory
(same flakiness as the event matrix; prompt/few-shot didn't fix it). The store
reader is adapter-agnostic, so one reliable driver suffices.

Precondition: memory tools are an enterprise opt-in -- without the entitlement the
tools error and the store-layer assertions fail.
"""

from __future__ import annotations

import pytest


from band.client.rest import (
    DEFAULT_REQUEST_OPTIONS,
    AgentMemoryCreateRequest,
    UnprocessableEntityError,
)
from band.core.memory_types import (
    MemoryListScope,
    MemorySegment,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    WorkingLongTermMemoryType,
)

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    MEMORY_AGENT,
    MEMORY_SECRETARY_AGENT,
    archive_memory_instruction,
    recall_memory_instruction,
    store_memory_instruction,
    store_subject_memory_inferred_instruction,
    store_subject_memory_instruction,
    store_two_memories_instruction,
    supersede_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.observations import MemoryTool
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    agent_rest_client,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_stored(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The store tool fired (call layer) and an agent-scoped memory landed in the
    store (store layer), both carrying our marker."""
    marker = unique_marker("mem")
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
            agent, scope=MemoryListScope.AGENT, content_query=marker
        )

    mem.calls.assert_store_called(
        content=marker,
        scope=MemoryStoreScope.AGENT,
        system=MemorySystem.LONG_TERM,
        type=WorkingLongTermMemoryType.SEMANTIC,
    )
    mem.stored.assert_stored(
        content=marker,
        scope=MemoryStoreScope.AGENT,
        system=MemorySystem.LONG_TERM,
        type=WorkingLongTermMemoryType.SEMANTIC,
    )


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_subject_scope(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A subject-scoped store about the agent itself, read back by subject_id.

    Exercises ``MemoryStoreScope.SUBJECT`` end to end plus ``where(subject_id=...)``
    filtering; the agent's own id is the subject, passed in the instruction.
    """
    marker = unique_marker("subjmem")
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


# Deliberately Agno, not the file's usual Anthropic driver: this is the retired
# legacy test's framework, and running it on a non-Anthropic adapter proves the
# scope+identity inference rides the adapter-agnostic injected memory guidance,
# not anything Anthropic-specific. Agno's builder uses the same capable Claude
# model, so it is no less reliable here.
@with_adapters(Adapter.AGNO, **MEMORY_SECRETARY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_subject_scope_inferred(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Subject scope + subject_id are *inferred*, not spelled out.

    The other subject test hands the agent ``scope=subject`` and the literal
    ``subject_id``. Here a generic "remember this about me personally" message
    names neither: the agent must classify the memory as subject-scoped and
    resolve *the user's own* id (via ``band_get_participants``/
    ``band_lookup_peers``) from the adapter's injected memory guidance alone.
    Correct = a subject memory carrying the marker whose ``subject_id`` is the
    real user id (``user_ops.whoami()``), proving the agent resolved the right
    identity rather than inventing or omitting one.
    """
    marker = unique_marker("subjinfer")
    user_id = await user_ops.whoami()
    room_id = await resource_manager.provision_room(
        title="e2e-memory-subject-inferred", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            store_subject_memory_inferred_instruction(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        mem = await capture.memory(
            agent,
            scope=MemoryListScope.SUBJECT,
            subject_id=user_id,
            content_query=marker,
        )

    mem.calls.assert_store_called(
        content=marker,
        scope=MemoryStoreScope.SUBJECT,
        subject_id=user_id,
    )
    mem.stored.assert_stored(content=marker, scope=MemoryStoreScope.SUBJECT)
    mem.stored.where(subject_id=user_id).assert_present()


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_excluded_from_general_tool_view(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Memory tool calls are opted out of the general ``tool_calls()`` view by
    default, but reachable via ``include_memory=True``, ``named()``, and ``memory()``."""
    marker = unique_marker("mem")
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
        with_memory = await capture.tool_calls(sender_id=agent.id, include_memory=True)
        mem = await capture.memory(
            agent, scope=MemoryListScope.AGENT, content_query=marker
        )

    # Excluded from the general view by default...
    assert not general.fired(MemoryTool.STORE), (
        f"memory tool leaked into the general view: {[c.name for c in general]}"
    )
    # ...but present when opted in, via the named() subset, and via memory().
    with_memory.assert_fired(MemoryTool.STORE)
    with_memory.named(MemoryTool.STORE).assert_fired(MemoryTool.STORE)
    mem.calls.assert_store_called(content=marker)


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_lifecycle_supersede(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then supersede in one turn: both ops fire and the record ends up
    superseded, demonstrating the lifecycle tools and the ``status`` dimension."""
    marker = unique_marker("lifemem")
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
            scope=MemoryListScope.AGENT,
            content_query=marker,
            status=MemoryStatus.ALL,
        )

    # Call layer: both lifecycle operations fired.
    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_supersede_called()
    # Store layer: the record is now superseded, not active.
    mem.stored.where(status=MemoryStatus.SUPERSEDED).assert_present()
    mem.stored.where(status=MemoryStatus.ACTIVE).assert_none()


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_lifecycle_archive(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then archive in one turn: the record ends up archived, not active."""
    marker = unique_marker("arcmem")
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
            scope=MemoryListScope.AGENT,
            content_query=marker,
            status=MemoryStatus.ALL,
        )

    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_archive_called()
    mem.stored.where(status=MemoryStatus.ARCHIVED).assert_present()
    mem.stored.where(status=MemoryStatus.ACTIVE).assert_none()


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_recall(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Store then recall: the read-side list and get tools fire (call layer)."""
    marker = unique_marker("recall")
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
            agent, scope=MemoryListScope.AGENT, content_query=marker
        )

    mem.calls.assert_store_called(content=marker)
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()
    mem.stored.assert_stored(content=marker)


@with_adapters(Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_store_layer_filtering(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two memories sharing a marker but differing in system/type: one read,
    sliced by dimension with where()."""
    marker = unique_marker("multi")
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
            agent, scope=MemoryListScope.AGENT, content_query=marker
        )

    # Both landed; slice the single collection by dimension.
    mem.stored.assert_at_least(2)
    mem.stored.where(system=MemorySystem.LONG_TERM).assert_stored(
        content=marker, type=WorkingLongTermMemoryType.SEMANTIC
    )
    mem.stored.where(system=MemorySystem.WORKING).assert_stored(
        content=marker, type=WorkingLongTermMemoryType.EPISODIC
    )


@with_adapters(Adapter.ANTHROPIC, Adapter.ANTHROPIC, **MEMORY_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_agent_scope_isolated_across_agents(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """An agent-scoped memory is private to the agent that stored it -- a
    different agent's own agent-scoped read never returns it, even filtered to
    the same marker. This is the isolation boundary PLT-1396 hardened
    (organization-scoped reads used to leak cross-tenant when the reading
    agent's owner had no organization); agent scope has no organization_id to
    go missing, so nothing but the identity filter keeps this true -- worth
    locking down directly."""
    agent_w, agent_r = agents
    marker = unique_marker("xagent")
    room_w = await resource_manager.provision_room(
        title="e2e-memory-xagent-writer", participants=[agent_w.id]
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
            agent_w, scope=MemoryListScope.AGENT, content_query=marker
        )

    # Different agent, different room: read through the reader's own client. No
    # turn is needed -- the writer's store is already durable.
    room_r = await resource_manager.provision_room(
        title="e2e-memory-xagent-reader", participants=[agent_r.id]
    )
    async with reply_capture(room_r) as cap_r:
        mem_r = await cap_r.memory(
            agent_r, scope=MemoryListScope.AGENT, content_query=marker
        )

    # Writer stored it (both layers).
    mem_w.calls.assert_store_called(content=marker)
    mem_w.stored.assert_stored(content=marker)
    # Reader, a different agent in a different room, sees none of the writer's
    # agent-scoped memory.
    mem_r.stored.assert_none()
    assert not mem_r.calls, "reader should not have called any memory tool"


@pytest.mark.asyncio(loop_scope="session")
async def test_memory_organization_scope_rejected_on_list(
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> None:
    """A read explicitly asking for organization scope gets a real 422 from the
    live platform, not a silent empty 200, when the agent's owner belongs to no
    organization -- true of every agent this baseline provisions (see
    INT-1307). This is the exact contract PLT-1396 shipped: before it, a caller
    in this state could read every organization-scoped memory in the database,
    because the tenancy filter silently dropped when organization_id was nil.
    A direct REST call against a provisioned-but-never-run identity -- no LLM
    turn needed, deterministic, and it exercises the live platform on every run."""
    agent = await resource_manager.provision_agent("org-scope-list-rejected")
    client = agent_rest_client(agent, baseline_settings)

    with pytest.raises(UnprocessableEntityError) as exc_info:
        await client.agent_api_memories.list_agent_memories(
            scope=MemoryListScope.ORGANIZATION.value,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    assert exc_info.value.body["error"]["code"] == "org_scope_requires_organization"


@pytest.mark.asyncio(loop_scope="session")
async def test_memory_organization_scope_rejected_on_store(
    resource_manager: ResourceManager,
    baseline_settings: BaselineSettings,
) -> None:
    """The write-side twin of the read rejection above: storing with an
    explicit organization scope 422s for an agent whose owner has no
    organization, rather than silently minting an unreadable orphan row."""
    agent = await resource_manager.provision_agent("org-scope-store-rejected")
    client = agent_rest_client(agent, baseline_settings)

    with pytest.raises(UnprocessableEntityError) as exc_info:
        await client.agent_api_memories.create_agent_memory(
            memory=AgentMemoryCreateRequest(
                content=unique_marker("orgreject"),
                system=MemorySystem.LONG_TERM.value,
                type=WorkingLongTermMemoryType.SEMANTIC.value,
                segment=MemorySegment.USER.value,
                thought="probing the organization-scope guard",
                scope=MemoryStoreScope.ORGANIZATION.value,
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    assert "organization_id" in exc_info.value.body["error"]["details"]
