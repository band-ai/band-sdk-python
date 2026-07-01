"""Emitted-event smokes: drive a deterministic ``band_send_event`` emission and
assert, from the agent's persisted events, what was emitted and with what content
(``ReplyCapture.thoughts`` / ``errors`` / ``tasks``).

Agents run with **no emit features**, so the only events on the wire are the ones
driven. Assert the injected **marker**, not bare presence: adapters auto-emit a
generic ``error`` event on any turn exception.

Anthropic-only: gpt-5.4-mini (LangGraph) is unreliable at *choosing* to call
``band_send_event`` (~50% even after a tool-only-channel system prompt + few-shot
examples). The observation readers are adapter-agnostic, so one reliable driver
suffices. The agents are injected by ``@with_adapters`` under the exact-execution
prompt so the only action they take is the requested tool call.
"""

from __future__ import annotations


import pytest

from band.core.types import MessageType

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    emit_thoughts_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.user_ops import UserOps


EVENT_TYPES = [MessageType.THOUGHT, MessageType.ERROR, MessageType.TASK]


@with_adapters(Adapter.ANTHROPIC, **TOOL_AGENT)
@pytest.mark.parametrize("event_type", EVENT_TYPES, ids=lambda mt: mt.value)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_emitted(
    event_type: MessageType,
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Each event type: it is emitted and carries our marker."""
    marker = unique_marker(event_type.value)
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

    events.assert_present()
    events.assert_contains_any([marker])


@with_adapters(Adapter.ANTHROPIC, **TOOL_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_subclasses_one_turn(
    agent: ProvisionedAgent,
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


@with_adapters(Adapter.ANTHROPIC, **TOOL_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_thoughts_assert_at_least(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two thoughts in one turn: demonstrates the count-floor assertion."""
    first = unique_marker("th1")
    second = unique_marker("th2")
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


@with_adapters(Adapter.ANTHROPIC, Adapter.ANTHROPIC, **TOOL_AGENT)
@pytest.mark.asyncio(loop_scope="session")
async def test_event_sender_isolation(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two agents emit thoughts in one room; thoughts(sender_id=X) returns only
    X's, demonstrating per-sender scoping of the event readers."""
    agent_a, agent_b = agents
    marker_a = unique_marker("th-a")
    marker_b = unique_marker("th-b")
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
