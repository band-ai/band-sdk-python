"""Letta-lane showcase smokes — the toolkit driving the Letta adapter live.

These are deliberately Letta-focused (unlike the generic matrix, which runs every
adapter through one scenario): they demonstrate the toolkit against the ``letta``
CI lane and exercise what is specific to Letta — its server-side stateful per-room
agent and per-room isolation.

The lane runs Letta in **auto-relay mode** (no Band MCP server): a self-hosted
Letta server can't reach an in-process MCP bound to a private/loopback IP (its
SSRF guard rejects non-public IPs, and stdio MCP isn't registrable via the API),
so the adapter relays the model's plain-text reply to the room itself. The MCP
tool-execution path is covered by the mocked adapter unit tests instead.

Both tests are bound to ``@with_agents(Adapter.LETTA)``, so they run in the
``backends`` lane (and the full local matrix) and skip-with-reason elsewhere.

Run with:

    E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run pytest \\
        tests/e2e/baseline/smoke/test_letta.py -v -s --no-cov
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.e2e.baseline.agents import Adapter, with_agents
from tests.e2e.baseline.toolkit.assertions import assert_contains_any, assert_present
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

JudgeFn = Callable[..., Awaitable[Verdict]]

# A conversational steer for the recall/isolation cases: reply naturally and lean
# on what the agent remembers from earlier in the same room.
_CONVERSATIONAL = (
    "You are a friendly assistant in a chat room. Reply in one short sentence, "
    "and use what you remember from earlier in this conversation."
)


@with_agents(Adapter.LETTA, prompt=_CONVERSATIONAL)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_letta_recalls_across_turns(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
) -> None:
    """Letta's stateful per-room agent recalls a fact from an earlier turn.

    This is Letta's own in-agent conversation state (server-side), not the platform
    memory tools. Tell a fact, barrier, then ask for it next turn and scope the read
    to that turn with ``snapshot()``/``since()``.
    """
    room_id = await resource_manager.provision_room(
        title="e2e-letta-recall", participants=[agent.id]
    )
    mention = {"mention_id": agent.id, "mention_name": agent.name}

    async with reply_capture(room_id) as capture:
        first = await user_ops.send_message(
            room_id, "Remember: my favorite color is teal.", **mention
        )
        await capture.wait_for_processed(first, agent.id)

        # Scope the read to the recall turn: snapshot before asking, read since.
        mark = capture.messages.snapshot()
        question = await user_ops.send_message(
            room_id, "What is my favorite color?", **mention
        )
        await capture.wait_for_processed(question, agent.id)
        recall = capture.messages.since(mark)

    # Cheap structural pre-check before the (costlier) semantic judge.
    assert_present(recall, what="a recall reply")
    assert_contains_any(recall, ["teal"])

    verdict = await judge(
        criteria=(
            "The user earlier said their favorite color is teal. Pass only if the "
            "agent's reply recalls that the favorite color is teal."
        ),
        transcript=recall,
    )
    assert verdict.passed, f"{verdict.reasoning}\n{format_transcript(recall)}"


@with_agents(Adapter.LETTA, prompt=_CONVERSATIONAL)
@pytest.mark.timeout(120)
@pytest.mark.asyncio(loop_scope="session")
async def test_letta_rooms_are_isolated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """In per_room mode each room gets its own Letta agent, so state can't leak.

    A secret told only in room A must not appear in room B's reply — room B's Letta
    agent never saw it.
    """
    room_a = await resource_manager.provision_room(
        title="e2e-letta-isolation-a", participants=[agent.id]
    )
    room_b = await resource_manager.provision_room(
        title="e2e-letta-isolation-b", participants=[agent.id]
    )
    mention = {"mention_id": agent.id, "mention_name": agent.name}

    # Subscribe to both rooms before sending so neither turn can be missed.
    async with (
        reply_capture(room_a) as cap_a,
        reply_capture(room_b) as cap_b,
    ):
        # Tell the secret only in room A, settle it...
        m_a = await user_ops.send_message(
            room_a, "Remember: the secret passphrase is BLUEFOX.", **mention
        )
        await cap_a.wait_for_processed(m_a, agent.id)
        # ...then ask for it in room B, whose agent never heard it.
        m_b = await user_ops.send_message(
            room_b,
            "What is the secret passphrase I told you? If you don't know it, say so.",
            **mention,
        )
        await cap_b.wait_for_processed(m_b, agent.id)
        b_replies = list(cap_b.messages)

    assert_present(b_replies, what="a reply in room B")
    assert not any("BLUEFOX" in m.content for m in b_replies), (
        "room B's Letta agent leaked room A's secret — per_room isolation broken"
    )
