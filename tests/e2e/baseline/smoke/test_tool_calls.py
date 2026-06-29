"""Tool-observation smokes for the baseline toolkit.

Each drives a deterministic tool use and asserts, by inspecting the agent's tool
calls, what fired and with which args. This exercises the whole capture path: an
agent with execution reporting on emits ``tool_call`` events, read back via
``ReplyCapture.tool_calls`` and checked with ``ToolCalls.assert_fired``.

The tools are opaque (see ``sample_tools``) so the agent must call them rather
than answer from its own knowledge, which is what makes "did it fire" reliable.
Dedicated agents are built here (not the shared cheap-agent fixtures) so the
tools and execution reporting are opt-in exactly where the tool calls matter.

Turn completion uses the delivery-status barrier (``wait_for_processed``), not a
probe/echo: the platform marks the trigger message ``processed`` only after the
agent's reply is emitted, by which point the turn's ``tool_call`` events are
already persisted — so the subsequent ``tool_calls`` read is race-free.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.sample_tools import (
    ACCESS_CODES,
    LOOKUP,
    LOOKUP_AND_WEATHER_PROMPT,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
    WEATHER,
    WEATHER_TOOL,
    build_tool_agent,
)
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.capture import ReplyCapture

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]


@requires(Dep.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_tool_fired_with_args(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The proof: a single tool fires and is asserted with the right args."""
    adapter = build_tool_agent(
        baseline_settings, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT
    )
    async with running_provisioned_agent(adapter, resource_manager, label="lookup") as (
        _,
        agent,
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-tool-fired", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "look up the access code for key 'alpha'",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            # Delivery-status barrier: once the trigger is processed, the lookup
            # turn (its tool_call event included) is fully persisted.
            await capture.wait_for_processed(mid, agent.id)
            calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})


@requires(Dep.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_capture_exposes_replies_and_tool_calls(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One capture, both views: assert the reply (Replies) and the tool (ToolCalls)."""
    adapter = build_tool_agent(
        baseline_settings, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT
    )
    async with running_provisioned_agent(adapter, resource_manager, label="lookup") as (
        _,
        agent,
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-replies-and-tools", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "look up the access code for key 'beta' and tell me the code",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            calls = await capture.tool_calls(sender_id=agent.id)

    # The reply view: the agent reported the secret code, which it could only know
    # by calling the tool.
    capture.messages.assert_present()
    capture.messages.assert_contains_any([ACCESS_CODES["beta"]])
    # The tool-call view: the lookup tool fired.
    calls.assert_fired(LOOKUP, with_args={"key": "beta"})


@requires(Dep.ANTHROPIC)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_tools_in_one_turn(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A single turn fires two different tools; both are observed in one ToolCalls."""
    adapter = build_tool_agent(
        baseline_settings,
        tools=[LOOKUP_TOOL, WEATHER_TOOL],
        prompt=LOOKUP_AND_WEATHER_PROMPT,
    )
    async with running_provisioned_agent(
        adapter, resource_manager, label="multitool"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-multi-tool", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "look up the access code for key 'alpha' and get the weather for Zorath",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
    calls.assert_fired(WEATHER, with_args={"place": "Zorath"})


@requires(Dep.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_with_args_tolerant_match(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """assert_fired with_args is tolerant: case-insensitive substring + arg subset."""
    adapter = build_tool_agent(
        baseline_settings, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT
    )
    async with running_provisioned_agent(adapter, resource_manager, label="lookup") as (
        _,
        agent,
    ):
        room_id = await resource_manager.provision_room(
            title="e2e-tolerant-args", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "look up the access code for key 'Alpha', note 'urgent'",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            calls = await capture.tool_calls(sender_id=agent.id)

    # Tolerant: 'alph' is a case-insensitive substring of the actual key ('Alpha'),
    # and we omit `note` entirely (subset match, not exact-args).
    calls.assert_fired(LOOKUP, with_args={"key": "alph"})
