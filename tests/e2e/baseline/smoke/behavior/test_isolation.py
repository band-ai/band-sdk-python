"""Isolation smokes: tool trajectories don\'t mix across senders or rooms.

The tool-call read is scoped two ways and both are shown here:
- ``sender_id`` keeps one agent\'s calls out of another\'s (same room).
- reading per room keeps one room\'s calls out of another\'s (same agent).

Uses the opaque tools from ``sample_tools`` so each agent must actually call its
tool (and with a distinguishing arg). Turn completion uses the delivery-status
barrier (``wait_for_processed``) on the id each ``send_message`` returns: once that
id is processed, the turn\'s ``tool_call`` events are persisted and the read is
race-free.

The single-agent cases use ``@with_adapters(Adapter.ANTHROPIC, tools=[...], ...)``.
``test_tool_calls_isolated_per_sender`` keeps a bespoke build: it needs two agents
with *different* tools in one room, which a single uniform ``@with_adapters`` set
cannot express.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_tools import (
    EXECUTION_REPORTING,
    LOOKUP,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
    WEATHER,
    WEATHER_PROMPT,
    WEATHER_TOOL,
)
from tests.e2e.baseline.toolkit.adapters import build_adapter
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@requires(Dep.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_tool_calls_isolated_per_sender(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Two agents share a room; each sender sees only its own tool calls.

    Bespoke (not ``@with_adapters``): the two agents need *different* tools, which one
    uniform decorator set cannot express — so build each explicitly via the registry
    (``build_adapter``) and run them with ``running_provisioned_agent``.
    """
    lookup_agent = build_adapter(
        Adapter.ANTHROPIC,
        baseline_settings,
        tools=[LOOKUP_TOOL],
        prompt=LOOKUP_PROMPT,
        **EXECUTION_REPORTING,
    )
    weather_agent = build_adapter(
        Adapter.ANTHROPIC,
        baseline_settings,
        tools=[WEATHER_TOOL],
        prompt=WEATHER_PROMPT,
        **EXECUTION_REPORTING,
    )
    async with contextlib.AsyncExitStack() as stack:
        a = await stack.enter_async_context(
            running_provisioned_agent(lookup_agent, resource_manager, label="lookup")
        )
        b = await stack.enter_async_context(
            running_provisioned_agent(weather_agent, resource_manager, label="weather")
        )
        room_id = await resource_manager.provision_room(
            title="e2e-sender-isolation", participants=[a.id, b.id]
        )
        async with reply_capture(room_id) as capture:
            m_a = await user_ops.send_message(
                room_id,
                "look up the access code for key 'alpha'",
                mention_id=a.id,
                mention_name=a.name,
            )
            await capture.wait_for_processed(m_a, a.id)
            m_b = await user_ops.send_message(
                room_id,
                "get the weather for Zorath",
                mention_id=b.id,
                mention_name=b.name,
            )
            await capture.wait_for_processed(m_b, b.id)
            # Sends/barriers stay sequential (one shared capture, one waiter), but
            # the two reads are independent REST calls, so gather them.
            a_calls, b_calls = await asyncio.gather(
                capture.tool_calls(sender_id=a.id),
                capture.tool_calls(sender_id=b.id),
            )

    a_calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
    assert not a_calls.fired(WEATHER), "lookup agent's tool calls leaked a weather call"
    b_calls.assert_fired(WEATHER, with_args={"place": "Zorath"})
    assert not b_calls.fired(LOOKUP), "weather agent's tool calls leaked a lookup call"


@with_adapters(
    Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.asyncio(loop_scope="session")
async def test_tool_calls_isolated_per_room(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One agent in two rooms; each room sees only its own tool call."""
    room_one = await resource_manager.provision_room(
        title="e2e-room-isolation-1", participants=[agent.id]
    )
    room_two = await resource_manager.provision_room(
        title="e2e-room-isolation-2", participants=[agent.id]
    )
    # Subscribe to both rooms before sending, so neither turn can be missed.
    async with (
        reply_capture(room_one) as cap_one,
        reply_capture(room_two) as cap_two,
    ):
        # The two captures are independent (separate nudges), so both rooms can run
        # concurrently: send both, settle both, read both.
        m_one, m_two = await asyncio.gather(
            user_ops.send_message(
                room_one,
                "look up the access code for key 'alpha'",
                mention_id=agent.id,
                mention_name=agent.name,
            ),
            user_ops.send_message(
                room_two,
                "look up the access code for key 'beta'",
                mention_id=agent.id,
                mention_name=agent.name,
            ),
        )
        await asyncio.gather(
            cap_one.wait_for_processed(m_one, agent.id),
            cap_two.wait_for_processed(m_two, agent.id),
        )
        calls_one, calls_two = await asyncio.gather(
            cap_one.tool_calls(sender_id=agent.id),
            cap_two.tool_calls(sender_id=agent.id),
        )

    calls_one.assert_fired(LOOKUP, with_args={"key": "alpha"})
    assert not any(c.args.get("key") == "beta" for c in calls_one), (
        "room one's tool calls leaked room two's call"
    )
    calls_two.assert_fired(LOOKUP, with_args={"key": "beta"})
    assert not any(c.args.get("key") == "alpha" for c in calls_two), (
        "room two's tool calls leaked room one's call"
    )


@with_adapters(
    Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.flaky(
    reruns=2, rerun_except=["AssertionError"]
)  # retry a transient live-turn timeout; assertion failures fail loud
@pytest.mark.asyncio(loop_scope="session")
async def test_capture_scopes_to_current_turn(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Reusing one capture across turns: the wait and the read scope to the new turn."""
    room_id = await resource_manager.provision_room(
        title="e2e-turn-scope", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        # Turn 1.
        m_one = await user_ops.send_message(
            room_id,
            "look up the access code for key 'alpha'",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        # Wait for turn 1's reply to be captured — turn_boundary() reads its timestamp
        # and raises on an empty buffer, so the processed signal alone isn't enough.
        await capture.wait_for_reply(m_one, agent.id, sender_id=agent.id)
        # Boundary between turns (server timestamp of turn 1's reply). Turn 1's call
        # is verified by the unscoped read at the end.
        boundary = capture.turn_boundary()

        # Turn 2, same capture reused. Barriering on turn 2's own id scopes the wait
        # to the new turn — it can't return on turn 1's reply.
        m_two = await user_ops.send_message(
            room_id,
            "now look up the access code for key 'beta'",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(m_two, agent.id)
        # Contrast a scoped read against an unscoped one to prove `since` (not some
        # other effect) is what excludes turn 1.
        turn_two, all_calls = await asyncio.gather(
            capture.tool_calls(sender_id=agent.id, since=boundary),
            capture.tool_calls(sender_id=agent.id),
        )

    # Unscoped, the room still holds BOTH turns' calls — so turn 1's is present...
    all_calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
    all_calls.assert_fired(LOOKUP, with_args={"key": "beta"})
    # ...but `since` scopes the read to turn 2, so turn 1's call is excluded.
    turn_two.assert_fired(LOOKUP, with_args={"key": "beta"})
    assert not any(c.args.get("key") == "alpha" for c in turn_two), (
        "turn-2 read leaked turn-1's call despite `since`"
    )
