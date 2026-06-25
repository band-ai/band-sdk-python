"""Agent scenario smokes that exercise the baseline tools end to end.

Each test drives real agents through the toolkit — provisioning (provision/reap),
the user-operations driver, the event-driven waiter + token-barrier drain, and
the LLM judge — and asserts a tolerant, behavioural outcome. These validate the
tools, not any L-level contract.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest

from band.core.simple_adapter import SimpleAdapter

from tests.e2e.baseline.toolkit.assertions import (
    assert_contains_any,
    assert_present,
)
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_provisioned_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.waiting import ReplyCapture, drain

CaptureFactory = Callable[[str], AbstractAsyncContextManager[ReplyCapture]]
JudgeFn = Callable[..., Awaitable[Verdict]]


@pytest.mark.asyncio(loop_scope="session")
async def test_two_agents_greet_each_other(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
    langgraph_adapter: SimpleAdapter,
    anthropic_adapter: SimpleAdapter,
) -> None:
    async with contextlib.AsyncExitStack() as stack:
        _, a = await stack.enter_async_context(
            running_provisioned_agent(langgraph_adapter, resource_manager, label="lg")
        )
        _, b = await stack.enter_async_context(
            running_provisioned_agent(
                anthropic_adapter, resource_manager, label="anthropic"
            )
        )

        room_id = await resource_manager.provision_room(
            title="e2e-mutual-greeting", participants=[a.id, b.id]
        )

        async with reply_capture(room_id) as capture:
            # User asks each agent, in turn, to greet the other.
            await user_ops.send_message(
                room_id,
                f"please say hello to {b.name}",
                mention_id=a.id,
                mention_name=a.name,
            )
            await capture.wait_for_sender(a.id)

            await user_ops.send_message(
                room_id,
                f"please say hello to {a.name}",
                mention_id=b.id,
                mention_name=b.name,
            )
            await capture.wait_for_sender(b.id)

            await drain(
                capture, user_ops, room_id, mention_id=a.id, mention_name=a.name
            )
            transcript = list(capture.messages)

    # Cheap structural pre-checks before the (costlier) semantic judge.
    assert_present(transcript)
    sender_ids = {m.sender_id for m in transcript}
    assert {a.id, b.id} <= sender_ids, (
        f"expected a reply from both agents; saw senders {sender_ids}"
    )

    verdict = await judge(
        criteria=(
            "Two agents share a room. The transcript should show BOTH agents "
            "producing a greeting (e.g. 'hello', 'hi') directed at the other. "
            "Pass only if both agents greeted."
        ),
        transcript=transcript,
    )
    assert verdict.passed, f"{verdict.reasoning}\n{format_transcript(transcript)}"


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_recalls_earlier_facts(
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
    anthropic_adapter: SimpleAdapter,
) -> None:
    """A burst of facts, a drain to settle, then a recall — judged tolerantly."""
    async with running_provisioned_agent(
        anthropic_adapter, resource_manager, label="anthropic"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-recall", participants=[agent.id]
        )
        mention = {"mention_id": agent.id, "mention_name": agent.name}

        async with reply_capture(room_id) as capture:
            # Burst: tell the agent a couple of facts, then drain to settle it.
            await user_ops.send_message(
                room_id, "Remember: my favorite color is teal.", **mention
            )
            await user_ops.send_message(
                room_id, "Also remember: my dog is named Pixel.", **mention
            )
            await drain(capture, user_ops, room_id, **mention)

            # Ask it to recall, then drain again so the answer is fully settled.
            # Everything captured from here on is the agent's recall turn. Drop
            # only the pure drain echo (a message that is *just* the nonce) —
            # matching on exact content, not substring, so a reply that both
            # recalls and happens to include the nonce is still judged.
            settled = len(capture.messages)
            await user_ops.send_message(
                room_id, "What is my favorite color and my dog's name?", **mention
            )
            nonce = await drain(capture, user_ops, room_id, **mention)
            recall = [
                m for m in capture.messages[settled:] if m.content.strip() != nonce
            ]

    # Cheap structural pre-check: the recall turn mentions at least one fact.
    assert_present(recall, what="a recall reply")
    assert_contains_any(recall, ["teal", "Pixel"])

    verdict = await judge(
        criteria=(
            "The user earlier said their favorite color is teal and their dog is "
            "named Pixel. Pass only if the agent's reply recalls BOTH facts "
            "(teal and Pixel)."
        ),
        transcript=recall,
    )
    assert verdict.passed, f"{verdict.reasoning}\n{format_transcript(recall)}"
