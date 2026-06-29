"""Agent scenario smokes that exercise the baseline tools end to end.

Each test drives real agents through the toolkit — provisioning (provision/reap),
the user-operations driver, the delivery-status processing barrier, and the LLM
judge — and asserts a tolerant, behavioural outcome. These validate the tools,
not any L-level contract.
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
from tests.e2e.baseline.toolkit.capture import ReplyCapture

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
            # User asks each agent, in turn, to greet the other. Barrier on each
            # trigger being processed — when that returns the greeting is already
            # captured (processed is reported only after the reply is emitted).
            m_a = await user_ops.send_message(
                room_id,
                f"please say hello to {b.name}",
                mention_id=a.id,
                mention_name=a.name,
            )
            await capture.wait_for_processed(m_a, a.id)

            m_b = await user_ops.send_message(
                room_id,
                f"please say hello to {a.name}",
                mention_id=b.id,
                mention_name=b.name,
            )
            await capture.wait_for_processed(m_b, b.id)

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
    """A burst of facts, a barrier to settle, then a recall — judged tolerantly."""
    async with running_provisioned_agent(
        anthropic_adapter, resource_manager, label="anthropic"
    ) as (_, agent):
        room_id = await resource_manager.provision_room(
            title="e2e-recall", participants=[agent.id]
        )
        mention = {"mention_id": agent.id, "mention_name": agent.name}

        async with reply_capture(room_id) as capture:
            # Burst: tell the agent a couple of facts without waiting between
            # them, then barrier on the last one. FIFO means once it is
            # processed, both facts were handled.
            await user_ops.send_message(
                room_id, "Remember: my favorite color is teal.", **mention
            )
            last = await user_ops.send_message(
                room_id, "Also remember: my dog is named Pixel.", **mention
            )
            await capture.wait_for_processed(last, agent.id)

            # Ask it to recall. Snapshot first, barrier on the question, then
            # slice — everything captured after the snapshot is the recall turn.
            snapshot = len(capture.messages)
            question = await user_ops.send_message(
                room_id, "What is my favorite color and my dog's name?", **mention
            )
            await capture.wait_for_processed(question, agent.id)
            recall = capture.messages[snapshot:]

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
