"""Agent scenario smokes that exercise the baseline tools end to end.

Each test drives real agents through the toolkit — provisioning (provision/reap),
the user-operations driver, the delivery-status processing barrier, and the LLM
judge — and asserts a tolerant, behavioural outcome. These validate the tools,
not any L-level contract.

The agents are injected by ``@with_agents(...)``: the decorator auto-applies the
requirement gate (from the registry) and the ``agent`` / ``agents`` fixtures build,
provision, run and reap them — so the body has no construction or lifecycle glue.
"""

from __future__ import annotations

import pytest

from collections.abc import Awaitable, Callable


from tests.e2e.baseline.agents import Adapter, with_agents
from tests.e2e.baseline.toolkit.assertions import (
    assert_contains_any,
    assert_present,
)
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.capture import CaptureFactory

JudgeFn = Callable[..., Awaitable[Verdict]]


@with_agents(Adapter.LANGGRAPH, Adapter.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_two_agents_greet_each_other(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
) -> None:
    a, b = agents
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


@with_agents(Adapter.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_recalls_earlier_facts(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
) -> None:
    """A burst of facts, a barrier to settle, then a recall — judged tolerantly."""
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

        # Snapshot the buffer, barrier on the question, then read what arrived
        # after — everything since the mark is the recall turn.
        mark = capture.messages.snapshot()
        question = await user_ops.send_message(
            room_id, "What is my favorite color and my dog's name?", **mention
        )
        await capture.wait_for_processed(question, agent.id)
        recall = capture.messages.since(mark)

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
