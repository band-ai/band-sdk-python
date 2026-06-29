"""Tool-observation smokes for the baseline toolkit.

Each drives a deterministic tool use and asserts, by inspecting the agent's tool
calls, what fired and with which args. The tools are opaque (see ``sample_tools``)
so the agent must call them rather than answer from its own knowledge, which is
what makes "did it fire" reliable. Agents run via ``@with_agents`` with the tools
attached and ``**EXECUTION_REPORTING`` so each tool call surfaces as a ``tool_call``
event, read back via ``ReplyCapture.tool_calls`` and checked with
``ToolCalls.assert_fired``.

Turn completion uses the delivery-status barrier (``wait_for_processed``): the
platform marks the trigger ``processed`` only after the reply is emitted, by which
point the turn's ``tool_call`` events are persisted — so the read is race-free.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, across_adapters, with_agents
from tests.e2e.baseline.smoke.sample_tools import (
    ACCESS_CODES,
    EXECUTION_REPORTING,
    LOOKUP,
    LOOKUP_AND_WEATHER_PROMPT,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
    WEATHER,
    WEATHER_TOOL,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_agents(
    Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_tool_fired_with_args(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The proof: a single tool fires and is asserted with the right args."""
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
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})


@with_agents(
    Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_capture_exposes_replies_and_tool_calls(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One capture, both views: assert the reply (Replies) and the tool (ToolCalls)."""
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


@with_agents(
    Adapter.ANTHROPIC,
    tools=[LOOKUP_TOOL, WEATHER_TOOL],
    prompt=LOOKUP_AND_WEATHER_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_tools_in_one_turn(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A single turn fires two different tools; both are observed in one ToolCalls."""
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


@with_agents(
    Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_with_args_tolerant_match(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """assert_fired with_args is tolerant: case-insensitive substring + arg subset."""
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


@across_adapters(
    include={Adapter.ANTHROPIC, Adapter.PYDANTIC_AI, Adapter.AGNO},
    tools=[LOOKUP_TOOL],
    prompt=LOOKUP_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_custom_tool_fires_across_frameworks(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The *same* ToolSpec drives a tool call on every tool-capable framework.

    ``@across_adapters`` builds ``LOOKUP_TOOL`` in each adapter's native format — band
    ``CustomToolDef`` for anthropic, a ``RunContext`` callable for pydantic-ai, an
    agent tool for agno — so the tool is defined once and the assertion path is one.
    """
    room_id = await resource_manager.provision_room(
        title=f"e2e-tool-{adapter_id}", participants=[matrix_agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "look up the access code for key 'alpha'",
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(mid, matrix_agent.id)
        calls = await capture.tool_calls(sender_id=matrix_agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})


@with_agents(
    Adapter.CREWAI, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_crewai_tool_fires(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """crewai custom-tool use via the same ToolSpec (the CustomToolDef path).

    Separate from ``test_custom_tool_fires_across_frameworks`` on purpose: crewai
    needs the ``dev-crewai`` lane, which is mutually exclusive with pydantic-ai, so
    the two can't be green in one lane. Auto-gated on ``Dep.CREWAI`` (runs in the
    dev-crewai lane); the call shape is identical to every other framework.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-tool-crewai", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "look up the access code for key 'alpha'",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
