"""Matrix scenario: peer-initiated delegation with self-recall, on two co-resident instances.

The thin L3 delegation slice, across the tool-loop matrix. Two instances A and B of the
same adapter co-reside via ``cell.run_many(2)``. Turn 1 seeds a value V into B's own
context. Turn 2 addresses B *directly* (not an orchestrator) — "ask A to confirm V and
report back" — so the delegation is B's own decision (peer-initiated). Load-bearing,
floors-only assertions from the one flow:

* Peer-initiated routing mention + self-recall (coupled): B emitted a real routing
  mention of A (by message metadata, not plain text) whose body carries the value B
  recalled from its OWN turn-1 context — coupled so the mention and the value are in one
  message.
* Delegate responded: A produced a reply (its turn is driven by B's mention, not a user
  send, so we barrier on A having spoken).

The round-trip value (B relaying A's computed result back to the user) is the flakiest
hop on a small model, so it is left as the plan's soft, non-gating tail. Named routing /
recruitment / concurrent triage are already covered by ``test_multi_agent_collaboration``.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REMEMBER,
    REPLY_PROMPT,
    delegate_to_peer_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(runs_tool_loop=True, prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2)  # multi-hop routing on a small model is non-deterministic
@pytest.mark.timeout(extra=300)  # a seed turn + a B→A→B delegation cascade
@pytest.mark.asyncio(loop_scope="session")
async def test_peer_initiated_delegation_with_self_recall(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """B recalls a seeded value, routes it to A by mention, and A responds."""
    value = unique_marker("value")
    async with cell.run_many(2) as (agent_a, agent_b):
        room_id = await resource_manager.provision_room(
            title=f"e2e-peer-delegation-{cell.adapter_id}",
            participants=[agent_a.id, agent_b.id],
        )
        # The delegation spans B→A, so budget a couple of turns off the per-turn default.
        cascade_deadline = baseline_settings.e2e_timeout * 2

        async with reply_capture(room_id) as capture:
            # Turn 1: seed the value into B's own context.
            seed_mid = await user_ops.send_message(
                room_id,
                REMEMBER.format(note=value),
                mention_id=agent_b.id,
                mention_name=agent_b.name,
            )
            await capture.wait_for_processed(seed_mid, agent_b.id)

            # Turn 2: ask B (directly) to delegate to A and report back.
            mark = capture.messages.snapshot()
            deleg_mid = await user_ops.send_message(
                room_id,
                delegate_to_peer_instruction(agent_a.name, agent_a.id),
                mention_id=agent_b.id,
                mention_name=agent_b.name,
            )
            await capture.wait_for_processed(deleg_mid, agent_b.id)
            # Coupled: B mentioned A (metadata) in a message carrying the recalled value
            # — a real peer-initiated routing mention off B's own context.
            capture.messages.since(mark).from_sender(agent_b.id).mentioning(
                agent_a.id
            ).assert_contains_any([value])

            # Cascade barrier: A's reply is driven by B's mention (not a user send), so
            # wait until A has produced a message *since the delegation* before asserting
            # it responded (scoped past `mark` so an earlier A message can't satisfy it).
            await capture.wait_until(
                lambda messages: any(
                    m.sender_id == agent_a.id for m in messages[mark:]
                ),
                deadline_s=cascade_deadline,
            )

        # The delegate responded to the delegation — the peer-initiated routing reached
        # A and it ran.
        capture.messages.since(mark).from_sender(agent_a.id).assert_present(
            what=f"a reply from delegate {agent_a.name}"
        )
