"""Grid tests for adapters that expose memory, contacts, or files capabilities.

Every cell comes from a capability filter, never a hard-coded adapter list:
``supports={Capability.MEMORY}`` selects the memory-capable adapters and
``without={Capability.MEMORY}`` the exact complement, so flipping an adapter's
``supports`` in the registry re-balances these tests automatically. Under
fail-never-skip a cell whose backend or key is absent ERRORs with that reason
(e.g. ``GOOGLE_API_KEY`` for gemini) — the honest "not wired up" signal, not a
regression.
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from band.core.memory_types import MemoryListScope
from band.core.types import Capability

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    CONTACTS_AGENT,
    FILES_AGENT,
    MEMORY_AGENT,
    file_round_trip_instruction,
    list_contacts_instruction,
    recall_memory_instruction,
    retrieve_memory_instruction,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.observations import ContactTool, FileTool
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
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
    """Store an agent-scoped memory through each memory-capable adapter."""
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
            agent, scope=MemoryListScope.AGENT, content_query=marker
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
    """Store, list, and fetch a memory through each memory-capable adapter.

    The fetch-by-id hop is what proves a real read-back: a list alone would also
    pass on a mis-wired read that returns nothing.
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
            agent, scope=MemoryListScope.AGENT, content_query=marker
        )

    mem.stored.assert_stored(content=marker)
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()


@per_adapter(
    supports={Capability.MEMORY},
    exclude=[
        ExcludedAdapter(
            Adapter.CREWAI,
            "the second, post-reboot retrieval turn returns an empty completion "
            "('Invalid response from LLM call - None or empty'), so the turn never "
            "finishes; reproduced on every attempt, not a transient",
        )
    ],
    **MEMORY_AGENT,
)
@flaky_infra("only transient failures")
@pytest.mark.timeout(extra=180)  # store, stop, fresh boot, list, get
@pytest.mark.asyncio(loop_scope="session")
async def test_memory_survives_adapter_rehydration(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A fresh adapter under one identity retrieves a memory from its prior run."""
    marker = unique_marker("rehydratemem")
    identity = await cell.provision(label=f"memory-rejoin-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-memory-rejoin-{cell.adapter_id}", participants=[identity.id]
    )

    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                store_memory_instruction(marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(mid, identity.id)

    retrieval_room_id = await resource_manager.provision_room(
        title=f"e2e-cap-memory-retrieve-{cell.adapter_id}", participants=[identity.id]
    )
    async with cell.run_as(identity):
        async with reply_capture(retrieval_room_id) as capture:
            mid = await user_ops.send_message(
                retrieval_room_id,
                retrieve_memory_instruction(marker),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(mid, identity.id)
            mem = await capture.memory(
                identity,
                scope=MemoryListScope.AGENT,
                content_query=marker,
            )

    # Assert the *effect* of the rehydrated recall, not how an adapter narrated it:
    # the marker coming back in the reply is what proves the fresh run reached the
    # prior run's memory. Requiring a specific ``content_query`` argument in the
    # narrated tool call instead made this hostage to per-adapter narration timing
    # (opencode reports a tool call once, on the first frame it sees, which for a
    # PENDING frame carries no arguments yet) and to whether the model chose to
    # filter server-side rather than list and read.
    replies.assert_contains_any([marker])
    mem.calls.assert_list_called()
    mem.calls.assert_get_called()
    mem.stored.assert_stored(content=marker)


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


@per_adapter(supports={Capability.FILES}, **FILES_AGENT)
@pytest.mark.timeout(extra=120)  # upload -> list -> read is a multi-tool turn
@pytest.mark.asyncio(loop_scope="session")
async def test_file_round_trip_across_files_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each files-capable adapter can send, discover, and read a room file."""
    marker = unique_marker("file")
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-files-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            file_round_trip_instruction(marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)
        results = await capture.tool_results(sender_id=agent.id)

    calls.assert_fired(FileTool.SEND.value)
    calls.assert_fired(FileTool.LIST.value)
    calls.assert_fired(FileTool.READ.value)
    results.assert_succeeded(FileTool.SEND.value)
    results.assert_succeeded(FileTool.LIST.value)
    results.assert_succeeded(FileTool.READ.value, output_contains=marker)
    replies.assert_contains_any([marker])


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
