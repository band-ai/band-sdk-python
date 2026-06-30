"""CrewAI showcase smokes — the toolkit driving the CrewAI adapters live.

CrewAI and CrewAI-Flow are ``crewai``-lane matrix adapters (their venv conflicts
with the default ``dev`` deps, so they install under the ``dev-crewai`` extra in
their own CI job). These are CrewAI-focused: ``CrewAIAdapter`` wraps a single
role/goal/backstory crew agent, and ``CrewAIFlowAdapter`` wraps a CrewAI Flow —
both re-offer the platform tools (and any custom tools) through their own runners.

Bound to ``@with_agents(Adapter.CREWAI / Adapter.CREWAI_FLOW)``, so they run in the
``crewai`` lane and skip-with-reason elsewhere. Locally they fail-loud unless the
``dev-crewai`` venv is active (``crewai`` not importable is the intended
"not wired up" signal under the matrix's fail-loud policy).

Run with:
    uv sync --extra dev-crewai
    E2E_TESTS_ENABLED=true BAND_E2E_LANE=crewai uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_crewai.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, with_agents
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    EXECUTION_REPORTING,
    LOOKUP,
    LOOKUP_PROMPT,
    LOOKUP_TOOL,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_agents(
    Adapter.CREWAI, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING
)
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
@pytest.mark.asyncio(loop_scope="session")
async def test_crewai_executes_custom_tool(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A custom ``ToolSpec`` is re-offered to the crew agent and actually fires.

    The lookup value is opaque, so a correct code in the reply proves the tool
    ran; the ``tool_call`` event confirms it fired with the expected argument.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-crewai-tool", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "look up the access code for key 'gamma'",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        calls = await capture.tool_calls(sender_id=agent.id)

    calls.assert_fired(LOOKUP, with_args={"key": "gamma"})
    capture.messages.assert_contains_any([ACCESS_CODES["gamma"]])


@with_agents(Adapter.CREWAI_FLOW)
@pytest.mark.flaky(reruns=2)  # a live agent turn occasionally times out; retry it
@pytest.mark.asyncio(loop_scope="session")
async def test_crewai_flow_replies(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A CrewAI Flow agent processes a message and posts a reply to the room.

    Unlike ``CrewAIAdapter`` this runs a Flow (not the Band tool loop) that returns
    a terminal result, so the assertion is just that the reply path delivered — a
    reply landed in the room and the delivery barrier cleared.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-crewai-flow", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            "Please say hello.",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)

    capture.messages.assert_present(what="a crewai-flow reply")
