"""Matrix scenario: per-room context isolation across every adapter.

A single agent joins two rooms; a distinct note is stated in each. When later
asked to recall the note, each room must return *its own* note and never the
other room's — proving every framework keeps per-room conversation state and the
platform scopes ``/context`` per room, with no cross-room leakage.

Replaces the now-removed legacy ``tests/e2e/scenarios/test_room_isolation.py``, on
the baseline toolkit: the agent comes from ``@per_adapter`` (full matrix, gated +
reaped), rooms/driving/capture from the toolkit, and the leak check is the tolerant
``assert_contains_none`` (the dual of ``assert_contains_any``) rather than a bespoke
helper.

Wording note: the payload is a neutral "note", not a "secret code" — models
reliably refuse to repeat a credential-shaped value, an unrelated false failure.
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_model

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    RECALL,
    REPLY_PROMPT,
    REMEMBER,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(
    exclude=[
        ExcludedAdapter(
            Adapter.CREWAI_FLOW,
            "terminal echo flow with no memory — cannot recall a per-room note",
        )
    ],
    prompt=REPLY_PROMPT,
)
@flaky_model(
    "recall on a live model is non-deterministic — the model occasionally denies "
    "having the note despite it being in context"
)
@pytest.mark.timeout(extra=180)  # four sequential turns (two rooms × state + recall)
@pytest.mark.asyncio(loop_scope="session")
async def test_rooms_keep_isolated_context(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """One agent in two rooms recalls each room's note without cross-leak.

    Fresh rooms per run keep rehydrated history small (a reused room would bloat
    into timeouts) and the per-run marker suffix makes the cross-room assertions
    impossible to satisfy by coincidence. Sends are sequential — a single agent
    processes one room at a time, so concurrent sends can time out.
    """
    note_a = unique_marker("alpha")
    note_b = unique_marker("bravo")
    room_a = await resource_manager.provision_room(
        title=f"e2e-isolation-a-{agent.adapter_id}", participants=[agent.id]
    )
    room_b = await resource_manager.provision_room(
        title=f"e2e-isolation-b-{agent.adapter_id}", participants=[agent.id]
    )

    # Phase 1: state a different note in each room.
    async with reply_capture(room_a) as cap_a:
        mid = await user_ops.send_message(
            room_a,
            REMEMBER.format(note=note_a),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await cap_a.wait_for_processed(mid, agent.id)
    async with reply_capture(room_b) as cap_b:
        mid = await user_ops.send_message(
            room_b,
            REMEMBER.format(note=note_b),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await cap_b.wait_for_processed(mid, agent.id)

    # Phase 2: each room recalls only its own note, never the other's. A fresh
    # capture per room scopes the assertion to the recall reply alone.
    async with reply_capture(room_a) as cap_a:
        mid = await user_ops.send_message(
            room_a,
            RECALL,
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await cap_a.wait_for_reply(mid, agent.id)
        replies.assert_contains_any([note_a])
        replies.assert_contains_none([note_b])

    async with reply_capture(room_b) as cap_b:
        mid = await user_ops.send_message(
            room_b,
            RECALL,
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await cap_b.wait_for_reply(mid, agent.id)
        replies.assert_contains_any([note_b])
        replies.assert_contains_none([note_a])
