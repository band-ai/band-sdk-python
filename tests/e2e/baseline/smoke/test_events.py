"""Emitted-event smokes: drive a deterministic ``band_send_event`` emission and
assert, from the agent's persisted events, what was emitted and with what content
(``ReplyCapture.thoughts`` / ``errors`` / ``tasks``).

The matrix runs every event type across every registered adapter (see
``sample_agents``). Agents run with **no emit features**, so the only events on
the wire are the ones driven. Assert the injected **marker**, not bare presence:
adapters auto-emit a generic ``error`` event on any turn exception.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.types import MessageType

from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.sample_agents import (
    adapter_params,
    build_agent,
    emit_event_instruction,
    emit_thoughts_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.user_ops import UserOps

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]

EVENT_TYPES = [MessageType.THOUGHT, MessageType.ERROR, MessageType.TASK]


# Anthropic-only: gpt-5.4-mini (LangGraph) is unreliable at *choosing* to call
# band_send_event (~50% even after a tool-only-channel system prompt + few-shot
# examples; only tool_choice/strict mode would fix it, which is adapter-level).
# The observation readers are adapter-agnostic, so one reliable driver suffices.
@pytest.mark.parametrize("adapter_id", adapter_params(include={"anthropic"}))
@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda mt: mt.value)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_emitted(
    adapter_id: str,
    event_type: MessageType,
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each event type, on each adapter: it is emitted and carries our marker."""
    marker = unique_marker(event_type.value)
    adapter = build_agent(adapter_id, baseline_settings)
    async with running_provisioned_agent(
        adapter, resource_manager, label=adapter_id
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-events", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                emit_event_instruction(event_type, marker),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            # Token-barrier: the FIFO echo proves the emit turn (its event POST
            # included) was fully processed and persisted before we read it.
            await capture.wait_for_processed(mid, agent.id)
            events = await capture.events(event_type, sender_id=agent.id)

    events.assert_emitted()
    events.assert_contains_any([marker])


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_subclasses_one_turn(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One turn emits all three types; each subclass view reads its own marker.

    Cheapest proof that the shared base read plus the three ``MESSAGE_TYPE``
    markers all work in a single capture.
    """
    thought = unique_marker("thought")
    task = unique_marker("task")
    error = unique_marker("error")
    instruction = (
        "Call band_send_event three times: "
        f"(1) message_type='thought' content including {thought}; "
        f"(2) message_type='task' content including {task}; "
        f"(3) message_type='error' content including {error}. "
        "Each token verbatim. Do not call any other tool."
    )
    adapter = build_agent("anthropic", baseline_settings)
    async with running_provisioned_agent(adapter, resource_manager, label="events") as (
        _,
        agent,
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-events-all", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, instruction, mention_id=agent.id, mention_name=agent.name
            )
            await capture.wait_for_processed(mid, agent.id)
            thoughts = await capture.thoughts(sender_id=agent.id)
            errors = await capture.errors(sender_id=agent.id)
            tasks = await capture.tasks(sender_id=agent.id)

    thoughts.assert_contains_any([thought])
    tasks.assert_contains_any([task])
    errors.assert_contains_any([error])


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_thoughts_assert_at_least(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two thoughts in one turn: demonstrates the count-floor assertion."""
    first = unique_marker("th1")
    second = unique_marker("th2")
    adapter = build_agent("anthropic", baseline_settings)
    async with running_provisioned_agent(
        adapter, resource_manager, label="thoughts"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-events-multi", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                emit_thoughts_instruction([first, second]),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            thoughts = await capture.thoughts(sender_id=agent.id)

    thoughts.assert_at_least(2)
    thoughts.assert_contains_any([first])
    thoughts.assert_contains_any([second])


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_sender_isolation(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two agents emit thoughts in one room; thoughts(sender_id=X) returns only
    X's, demonstrating per-sender scoping of the event readers."""
    marker_a = unique_marker("th-a")
    marker_b = unique_marker("th-b")
    adapter_a = build_agent("anthropic", baseline_settings)
    adapter_b = build_agent("anthropic", baseline_settings)
    async with (
        running_provisioned_agent(adapter_a, resource_manager, label="emit-a") as (
            _,
            agent_a,
        ),
        running_provisioned_agent(adapter_b, resource_manager, label="emit-b") as (
            _,
            agent_b,
        ),
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-events-isolation", participants=[agent_a.id, agent_b.id]
        )
        async with reply_capture(room_id) as capture:
            m_a = await user_ops.send_message(
                room_id,
                emit_event_instruction(MessageType.THOUGHT, marker_a),
                mention_id=agent_a.id,
                mention_name=agent_a.name,
            )
            m_b = await user_ops.send_message(
                room_id,
                emit_event_instruction(MessageType.THOUGHT, marker_b),
                mention_id=agent_b.id,
                mention_name=agent_b.name,
            )
            # Barrier each emit on its own agent: once both are processed, both
            # turns' thought events are persisted.
            await capture.wait_for_processed(m_a, agent_a.id)
            await capture.wait_for_processed(m_b, agent_b.id)
            thoughts_a = await capture.thoughts(sender_id=agent_a.id)
            thoughts_b = await capture.thoughts(sender_id=agent_b.id)

    thoughts_a.assert_contains_any([marker_a])
    thoughts_b.assert_contains_any([marker_b])
    assert all(marker_b not in t.content for t in thoughts_a), (
        "agent A's thought view leaked agent B's event"
    )
    assert all(marker_a not in t.content for t in thoughts_b), (
        "agent B's thought view leaked agent A's event"
    )
