"""Parlant showcase smokes — the toolkit driving the Parlant adapter live.

Parlant is intentionally NOT a baseline *matrix* adapter (it is listed in
``NON_AGENT_ADAPTERS``): it needs a running Parlant server and per-agent setup that
the registry's synchronous ``build_adapter`` seam can't express. So, unlike the
other adapter showcases, these bring up an in-process server via
``running_parlant_server`` (which owns the hang-free teardown, OpenAI NLP service,
and ephemeral ports), build the agent + adapter by hand, then hand the finished
adapter to the toolkit's ``running_provisioned_agent`` — so everything else (agent
provisioning, capture, the delivery-status barrier, reaping) is the same shared
plumbing as every other baseline test.

This module ``importorskip``s parlant, so it skips cleanly where parlant isn't
installed — e.g. the ``crewai`` lane, whose ``dev-crewai`` venv conflicts with
parlant. That is a *structural* absence (a venv that deliberately can't hold both),
the same class of skip the matrix's lane scoping performs, not the
"missing key = misconfiguration" case the fail-loud policy targets.

Run with:
    E2E_TESTS_ENABLED=true uv run pytest \\
        tests/e2e/baseline/smoke/adapters/test_parlant.py -v -s --no-cov
"""

from __future__ import annotations

import contextlib

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Parlant isn't in the matrix and its venv conflicts with the crewai lane, so a
# structural skip (not a fail) is correct where it isn't importable.
pytest.importorskip("parlant.sdk")

_SHORT = "You are a friendly assistant in a chat room. Reply in one short sentence."


@requires(
    Dep.OPENAI
)  # running_parlant_server uses the OpenAI NLP service (OPENAI_API_KEY)
@lane(Lane.CORE)
@flaky_infra("retry a transient live-turn timeout; assertion failures fail loud")
# Pytest markers are evaluated at import time; the runtime reply deadline below
# still comes from the ``baseline_settings`` fixture.
@pytest.mark.timeout(BaselineSettings().e2e_parlant_backstop_timeout())
@pytest.mark.asyncio(loop_scope="session")
async def test_parlant_replies(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A Parlant agent (in-process server) processes a message and replies.

    Construction is bespoke (server + parlant agent + adapter), but the run is the
    standard toolkit flow: ``running_provisioned_agent`` provisions and runs it,
    and the delivery barrier proves the turn completed before we read the reply.
    The ``AsyncExitStack`` closes the agent before the server (LIFO), so the server
    outlives the run; ``running_parlant_server`` then tears the server down without
    hanging on Parlant's serve-forever ``__aexit__``.
    """
    from band.adapters.parlant import ParlantAdapter

    from tests.e2e.baseline.toolkit.parlant_server import running_parlant_server

    async with contextlib.AsyncExitStack() as stack:
        # running_parlant_server fills in the OpenAI NLP service and fresh ephemeral
        # ports by default, and tears the server down without hanging.
        server = await stack.enter_async_context(running_parlant_server())
        parlant_agent = await server.create_agent(
            name="E2E Showcase Agent",
            description=(
                "A test agent for baseline E2E validation. Keep replies short."
            ),
        )
        adapter = ParlantAdapter(
            server=server, parlant_agent=parlant_agent, custom_section=_SHORT
        )
        agent = await stack.enter_async_context(
            running_provisioned_agent(adapter, resource_manager, label="parlant")
        )

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
            # in-process server, so it has a Parlant-specific reply deadline.
            replies = await capture.wait_for_reply(
                mid,
                agent.id,
                deadline_s=baseline_settings.e2e_parlant_reply_timeout,
            )

    replies.assert_present(what="a parlant reply")
