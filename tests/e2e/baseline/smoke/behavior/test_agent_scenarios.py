"""Agent scenario smokes that exercise the baseline tools end to end.

Each test drives real agents through the toolkit — provisioning (provision/reap),
the user-operations driver, the delivery-status processing barrier, and the LLM
judge — and asserts a tolerant, behavioural outcome. These validate the tools,
not any L-level contract.

The agents are injected by ``@with_adapters(...)``: the decorator auto-applies the
requirement gate (from the registry) and the ``agent`` / ``agents`` fixtures build,
provision, run and reap them — so the body has no construction or lifecycle glue.
"""

from __future__ import annotations

import pytest

from collections.abc import Awaitable, Callable


from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.observations import Replies
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.capture import CaptureFactory

JudgeFn = Callable[..., Awaitable[Verdict]]


@with_adapters(Adapter.LANGGRAPH, Adapter.ANTHROPIC)
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
        # User asks each agent, in turn, to greet the other. The reply barrier on
        # each trigger waits for that agent's greeting to actually be captured, so
        # both replies are in the transcript before we snapshot it.
        m_a = await user_ops.send_message(
            room_id,
            f"please say hello to {b.name}",
            mention_id=a.id,
            mention_name=a.name,
        )
        await capture.wait_for_reply(m_a, a.id, sender_id=a.id)

        m_b = await user_ops.send_message(
            room_id,
            f"please say hello to {a.name}",
            mention_id=b.id,
            mention_name=b.name,
        )
        await capture.wait_for_reply(m_b, b.id, sender_id=b.id)

        transcript = Replies(capture.messages)

    # Cheap structural pre-checks before the (costlier) semantic judge.
    transcript.assert_present()
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
