"""Matrix: reply via band_send_message must not also emit a duplicate chat reply.

Once the tool posts, text-fallback suppression is an SDK contract (ObservingTools /
adapter close) — deterministic. No ``flaky_model``: a missed tool call or a second
chat reply fails loud so the root cause is investigated, not absorbed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from band.core.types import MessageType
from band.runtime.tools import BAND_SEND_MESSAGE, is_room_posting_tool

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.observations.events import Events
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCalls
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

TOOL_FIRST_PROMPT = (
    "You are under test. When the user gives you a marker token, reply ONLY by "
    f"calling {BAND_SEND_MESSAGE} mentioning the user, with that exact marker in the "
    "message body. Do not send any other chat text."
)

# This turn's chat window must hold the tool post alone. Scoped via snapshot/
# since so a parallel matrix cell (or a reused capture) cannot inflate the count.
TURN_CHAT_CEILING = 1


def _metadata_value(event: object, key: str) -> Any | None:
    metadata = getattr(event, "metadata", None)
    if hasattr(metadata, "model_dump"):
        metadata = metadata.model_dump()
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _json_payload(event: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(getattr(event, "content", "")))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _result_matches_room_post(event: object, call_ids: set[str]) -> bool:
    payload = _json_payload(event)
    tool_name = payload.get("name") or _metadata_value(event, "tool_name")
    tool_call_id = payload.get("tool_call_id") or _metadata_value(event, "tool_call_id")
    return (tool_name is not None and is_room_posting_tool(str(tool_name))) or (
        tool_call_id in call_ids
    )


def _result_failed(event: object) -> bool:
    payload = _json_payload(event)
    status = payload.get("status") or _metadata_value(event, "status")
    output = str(payload.get("output") or getattr(event, "content", ""))
    return (
        status in {"failed", "error"}
        or payload.get("is_error") is True
        or output.startswith(("Error", "Invalid arguments"))
    )


def assert_successful_room_post_result(calls: ToolCalls, results: Events) -> None:
    """The room-posting tool completed, not merely started."""
    call_ids = {
        call.tool_call_id
        for call in calls.named(BAND_SEND_MESSAGE)
        if call.tool_call_id is not None
    }
    candidates = [
        event for event in results if _result_matches_room_post(event, call_ids)
    ]
    assert candidates, (
        f"expected a {BAND_SEND_MESSAGE} tool_result, "
        f"observed: {[event.content for event in results]}"
    )
    failures = [event.content for event in candidates if _result_failed(event)]
    assert not failures, f"{BAND_SEND_MESSAGE} reported failed result(s): {failures}"


def assert_tool_first_reply(
    calls: ToolCalls, results: Events, replies: Replies, *, marker: str
) -> None:
    """Tool posted the marker; this turn's chat window has no text-fallback twin."""
    calls.assert_fired(BAND_SEND_MESSAGE, with_args={"content": marker})
    assert_successful_room_post_result(calls, results)
    replies.assert_contains_any([marker])
    replies.assert_at_most(TURN_CHAT_CEILING)


@per_adapter(
    exclude=[
        ExcludedAdapter(
            Adapter.CREWAI_FLOW,
            "terminal echo flow does not execute Band tools",
        ),
        ExcludedAdapter(
            Adapter.LETTA,
            "server-side MCP tool execution is outside this text-fallback contract",
        ),
    ],
    prompt=TOOL_FIRST_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.timeout(extra=120)
@pytest.mark.asyncio(loop_scope="session")
async def test_band_send_message_is_the_only_chat_reply(
    agent: ProvisionedAgent,
    agent_room: str,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    marker = unique_marker("toolfirst")
    room_id = agent_room
    async with reply_capture(room_id) as capture:
        # Pre-send cursor: assert only on this turn's window under parallel cells.
        mark = capture.messages.snapshot()
        mid = await user_ops.mention(
            room_id,
            agent,
            f"Send the marker {marker} to me using {BAND_SEND_MESSAGE} only.",
        )
        replies = await capture.wait_for_reply(mid, agent.id, since=mark)
        # Fresh room + unique marker; still pin sender so a peer leak can't satisfy.
        calls = await capture.tool_calls(sender_id=agent.id)
        results = await capture.events(MessageType.TOOL_RESULT, sender_id=agent.id)

    assert_tool_first_reply(calls, results, replies, marker=marker)
