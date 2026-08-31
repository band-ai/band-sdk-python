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

import random
from collections.abc import Awaitable, Callable

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from band.core.memory_types import MemoryListScope
from band.core.types import Capability

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    CONTACTS_AGENT,
    FILES_AGENT,
    IMAGE_COLORS,
    MEMORY_AGENT,
    file_round_trip_instruction,
    image_round_trip_instruction,
    list_contacts_instruction,
    recall_memory_instruction,
    retrieve_memory_instruction,
    solid_color_png,
    store_memory_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.observations import ContactTool, FileTool
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

JudgeFn = Callable[..., Awaitable[Verdict]]


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


# Adapters with a verified real image-vision-passthrough fix -- keep this in
# sync with IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS in
# tests/framework_conformance/test_adapter_conformance.py (that's the unit-level
# source of truth; this list exists only because @per_adapter selects from the
# separate tests.baseline.adapter.Adapter enum, a different registry). Differs
# from that set by: -crewai_flow (its E2E builder is a hardcoded echo flow with
# no Band tool loop, so there's no tool call for this to drive), -parlant (not
# in Adapter at all -- confirmed unsupportable, no matrix cell), +copilot_acp
# (wraps ACPClientAdapter, which shares the same already-fixed MCP engine as
# opencode but isn't tracked in the unit-level set since it's not its own
# ADAPTER_CONFIGS entry there).
IMAGE_PASSTHROUGH_ADAPTERS = (
    Adapter.CLAUDE_SDK,
    Adapter.ANTHROPIC,
    Adapter.OPENCODE,
    Adapter.GEMINI,
    Adapter.LANGGRAPH,
    Adapter.AGNO,
    Adapter.STRANDS,
    Adapter.COPILOT_SDK,
    Adapter.COPILOT_ACP,
    Adapter.CODEX,
    Adapter.PYDANTIC_AI,
    Adapter.CREWAI,
    Adapter.LETTA,
)


@per_adapter(*IMAGE_PASSTHROUGH_ADAPTERS, **FILES_AGENT)
@pytest.mark.timeout(extra=120)  # upload -> list -> read -> vision reply
@pytest.mark.asyncio(loop_scope="session")
async def test_image_vision_passthrough_across_adapters(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
) -> None:
    """Each adapter with a verified image-passthrough fix can actually SEE an
    image a peer shared in the room, not just degrade it to descriptive text.

    The platform's file-upload endpoint is agent-scoped (no user-side upload),
    so a peer agent -- not UserOps -- plays "someone already shared a file
    here": PeerActor.send_file uploads a solid-color PNG built by
    solid_color_png() (pure stdlib, no Pillow dependency) and mentions the
    agent under test. The color is randomized per run and judged against
    ground truth, so a model can't pass by reflexively guessing a common
    default without actually looking at the pixels.
    """
    color = random.choice(sorted(IMAGE_COLORS))
    image = solid_color_png(color)

    bystander = await resource_manager.provision_agent(
        f"image-sender-{agent.adapter_id}"
    )
    room_id = await resource_manager.provision_room(
        title=f"e2e-cap-image-{agent.adapter_id}",
        participants=[agent.id, bystander.id],
    )
    async with reply_capture(room_id) as capture:
        mid = await resource_manager.peer(bystander).send_file(
            room_id,
            image,
            filename="swatch.png",
            content_type="image/png",
            caption=image_round_trip_instruction(),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)
        results = await capture.tool_results(sender_id=agent.id)

    calls.assert_fired(FileTool.LIST.value)
    calls.assert_fired(FileTool.READ.value)
    results.assert_succeeded(FileTool.LIST.value)
    results.assert_succeeded(FileTool.READ.value)

    verdict = await judge(
        criteria=(
            f"An agent was shown an image whose single dominant color is "
            f"{color} (RGB {IMAGE_COLORS[color]}). It was asked to identify "
            "that color in one word and reply with just it. Pass if the "
            "reply names that exact color or an unambiguous close synonym "
            "(e.g. 'crimson' for red, 'violet' for purple). Fail if the "
            "reply names a clearly different color, describes the image as "
            "unreadable or inaccessible, or otherwise shows it did not "
            "actually see the image content."
        ),
        transcript=replies,
    )
    assert verdict.passed, f"{verdict.reasoning}\n{format_transcript(replies)}"


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
