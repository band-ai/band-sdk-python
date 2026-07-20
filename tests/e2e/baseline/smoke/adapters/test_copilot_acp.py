"""Live smoke coverage for the outbound ACP room-visible tool contract.

The scenario is intentionally backend-neutral: it asks the ACP agent to emit one
Band event, then checks that the event was persisted and that the call was
narrated as an ordinary ACP tool call — like any other tool, with no special
suppression for Band messaging tools.
"""

from __future__ import annotations

import pytest

from band.core.types import MessageType

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A band_send_event call is narrated as an ACP tool_call, like any other tool.

    Uses the raw ``events`` reader (not the JSON-based ``tool_calls`` helper):
    ACP narrates a tool_call's content as the plain ACP-reported title (e.g.
    ``"band_send_event"``), not the ``{"name": ..., "args": ...}`` JSON shape
    other adapters use, so a substring check is the right tool here.
    """
    marker = unique_marker("acp-event")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-call-narrated", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_call_events = await capture.events(
            MessageType.TOOL_CALL, sender_id=agent.id
        )

    thoughts.assert_contains_any([marker])
    tool_call_events.assert_at_least(1)
    tool_call_events.assert_contains_any(["band_send_event"])


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_result_is_a_single_clean_payload(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A Band tool's tool_result event carries the tool's output exactly once.

    An MCP bridge that forwards both a result's readable text and its
    structuredContent companion into one block duplicates the payload -- the
    room event then reads as the same JSON twice (once readable, once
    re-encoded). The contract: the emitted tool_result content is a single
    well-formed JSON document, the platform's actual response.

    The marker proves the tool ran (via the thought it posted); it is NOT
    asserted inside the tool_result, because the platform's create-event
    response (``{id, message_type, success}``) does not echo the content. The
    JSON check is scoped to the Band tool's results (selected by the response's
    ``"success"`` field): Copilot also narrates its own internal tools (e.g.
    skill loading), whose results are legitimately plain text.
    """
    marker = unique_marker("acp-result")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-result-clean", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_results = await capture.events(MessageType.TOOL_RESULT, sender_id=agent.id)

    thoughts.assert_contains_any([marker])
    band_results = tool_results.containing('"success"')
    band_results.assert_at_least(1)
    band_results.assert_json_content()
