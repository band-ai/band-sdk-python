"""Parlant showcase smokes — the toolkit driving the Parlant adapter live.

Parlant is intentionally NOT a baseline *matrix* adapter (it is listed in
``NON_AGENT_ADAPTERS``), but its customer-facing adapter now owns the in-process
server and per-agent setup. This showcase builds that adapter directly, then hands
it to the toolkit's ``running_provisioned_agent`` so provisioning, capture, the
delivery-status barrier, and reaping use the same shared plumbing as every other
baseline test.

This module ``importorskip``s parlant, so it skips cleanly where parlant isn't
installed — e.g. the ``crewai`` lane, whose ``dev-crewai`` venv conflicts with
parlant, or any lane run with a plain ``dev`` venv (parlant now needs its own
``dev-parlant`` extra — see pyproject ``[tool.uv] conflicts``). That is a
*structural* absence (a venv that deliberately can't hold both), the same class of
skip the matrix's lane scoping performs, not the "missing key = misconfiguration"
case the fail-loud policy targets.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_parlant.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    LOOKUP,
    LOOKUP_TOOL,
    lookup_code_instruction,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Parlant isn't in the matrix and its venv conflicts with the crewai lane, so a
# structural skip (not a fail) is correct where it isn't importable.
pytest.importorskip("parlant.sdk")

import parlant.sdk as p

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig

_SHORT = "You are a friendly assistant in a chat room. Reply in one short sentence."

# The custom tool is reached only through a guideline that offers it.
_LOOKUP_CONDITION = "The user asks for the access code of a key"
LOOKUP_KEY = "alpha"
_LOOKUP_ACTION = (
    f"Call the {LOOKUP} tool with that key, then tell the user the code it returns"
)

LiveTest = Callable[..., Awaitable[None]]


def parlant_live_turn(test: LiveTest) -> LiveTest:
    """The lane, dependencies and budget every live Parlant turn needs.

    Parlant isn't in the adapter registry (NON_AGENT_ADAPTERS), so the lane
    selector can't derive its home lane and would run it in every lane; it is
    pinned to its own parlant lane (dev-parlant extra, split from core since
    parlant's griffe/griffelib transitive deps collide with pydantic-ai's).
    The adapter uses the OpenAI NLP service (OPENAI_API_KEY).

    The reply barrier waits up to ``e2e_timeout * 3`` (360s at the 120s
    default) for Parlant's cold multi-call pipeline. The outer cap is
    ``e2e_timeout + extra``, so ``extra`` must clear that barrier *plus*
    in-process server boot, provisioning, and teardown — otherwise the outer
    timeout hard-kills the test before the barrier can surface its diagnostic
    TimeoutError. 480 -> 600s outer, leaving 240s overhead.
    """
    for mark in (
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.timeout(extra=480),
        flaky_infra(
            "retry a transient live-turn timeout; assertion failures fail loud"
        ),
        requires(Dep.OPENAI),
        lane(Lane.PARLANT),
    ):
        test = mark(test)
    return test


def showcase_adapter(**kwargs: Any) -> ParlantAdapter:
    """An adapter-owned Parlant server and agent, as a customer builds one."""
    return ParlantAdapter(
        ParlantAdapterConfig(
            name="E2E Showcase Agent",
            description="A test agent for baseline E2E validation. Keep replies short.",
            custom_section=_SHORT,
        ),
        nlp_service=p.NLPServices.openai,
        **kwargs,
    )


@parlant_live_turn
async def test_parlant_replies(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A Parlant agent (in-process server) processes a message and replies.

    The adapter owns its server and creates its Parlant agent with
    ``custom_section`` in the ordinary customer-facing construction path. The
    shared toolkit provisions and runs it, and the delivery barrier proves the
    turn completed before we read the reply.
    """
    adapter = showcase_adapter()
    async with running_provisioned_agent(
        adapter, resource_manager, label="parlant"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-parlant-reply", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                "Please say hello.",
                mention_id=agent.id,
                mention_name=agent.name,
            )
            # Parlant's first turn runs a multi-LLM-call pipeline on a cold
            # in-process server, so give the barrier more than one per-turn budget.
            replies = await capture.wait_for_reply(
                mid, agent.id, deadline_s=baseline_settings.e2e_timeout * 3
            )

    replies.assert_present(what="a parlant reply")


@parlant_live_turn
async def test_parlant_executes_custom_tool(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A custom tool from ``additional_tools`` runs through Parlant's guideline flow.

    The reply carries an access code the model cannot guess, so it proves the
    whole live path: guideline match, tool call, room resolution, and reply.
    """
    adapter = showcase_adapter(additional_tools=[LOOKUP_TOOL.as_custom_tool_def()])
    adapter.add_guideline(condition=_LOOKUP_CONDITION, action=_LOOKUP_ACTION)
    async with running_provisioned_agent(
        adapter, resource_manager, label="parlant-tool"
    ) as agent:
        room_id = await resource_manager.provision_room(
            title="e2e-parlant-custom-tool", participants=[agent.id]
        )
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                lookup_code_instruction(LOOKUP_KEY),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            replies = await capture.wait_for_reply(
                mid, agent.id, deadline_s=baseline_settings.e2e_timeout * 3
            )

    replies.assert_contains_any([ACCESS_CODES[LOOKUP_KEY]])
