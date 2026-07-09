"""Matrix scenario: an agent recalls earlier context — in-session and after rejoin.

Two variants of the same fact, run across every adapter, exercising the two ways
prior context reaches the model:

* ``test_recalls_within_session`` — a two-turn conversation with one running
  agent: state a note, then ask for it back. Proves each framework's history
  converter rebuilds the prior turn from the room transcript.
* ``test_recalls_after_rejoin`` — state the note, stop the agent, start a *fresh*
  adapter under the same identity, then ask. The second run begins with no
  in-memory adapter state, so a correct recall can only have come from the
  platform rehydrating the room's history on bootstrap (``/context``).

``test_recalls_after_rejoin`` excludes ``codex`` / ``opencode``: those adapters
recover context on reboot by resuming their own backend session (a session id
persisted via task events), not by consuming platform ``/context`` as history — a
different mechanism, so a pass there would not validate the ``/context`` guarantee
the rejoin case asserts. The in-session case keeps the full matrix: it exercises
each framework's history conversion within one run, which every adapter does.

Replaces the now-removed legacy ``tests/e2e/scenarios/test_context_persistence.py``
(the rejoin case), on the baseline toolkit; the in-session case is the simpler
sibling. The rejoin lifecycle uses ``cell.run_as`` — a fresh adapter run under one
provisioned identity — entered twice for the stop→rejoin.

Wording note: a neutral "note", not a "secret code" (models refuse to echo a
credential-shaped value — an unrelated false failure).
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    RECALL,
    REMEMBER,
    REPLY_PROMPT,
    unique_marker,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


# CREWAI_FLOW is a terminal echo flow with no memory (like codex/opencode), so it
# cannot recall a note stated on an earlier turn — exclude it from recall scenarios.
@per_adapter(exclude={Adapter.CREWAI_FLOW}, prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=120)  # two turns (state, then recall)
@pytest.mark.asyncio(loop_scope="session")
async def test_recalls_within_session(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Turn 2 recalls a note stated in turn 1 (in-session history conversion)."""
    note = unique_marker("note")
    room_id = await resource_manager.provision_room(
        title=f"e2e-recall-session-{agent.adapter_id}", participants=[agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            REMEMBER.format(note=note),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)

        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            RECALL,
            mention_id=agent.id,
            mention_name=agent.name,
        )
        replies = await capture.wait_for_reply(mid, agent.id, since=mark)
        replies.assert_contains_any([note])


@per_adapter(
    exclude={Adapter.CODEX, Adapter.OPENCODE, Adapter.CREWAI_FLOW}, prompt=REPLY_PROMPT
)
# Cold-boot recall is model-non-deterministic (the model occasionally denies having
# the note despite it being in the rehydrated context), so allow AssertionError reruns
# here — unlike the matrix's usual rerun_except; a real regression still fails red.
@pytest.mark.flaky(reruns=2)
@pytest.mark.timeout(extra=180)  # two agent startups (state, then rejoin)
@pytest.mark.asyncio(loop_scope="session")
async def test_recalls_after_rejoin(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A fresh adapter under the same identity recalls via platform rehydration.

    Requests ``cell`` so the test owns the agent lifecycle: it provisions the identity
    once, then runs a fresh adapter twice via ``cell.run_as`` — a stop→rejoin. The
    second run starts with no in-memory adapter state, so a correct recall can only
    have come from the platform rehydrating the room on bootstrap. The prompt set on
    ``@per_adapter`` is carried by the cell, so each run needs no per-call steering.
    The per-cell ``@requires`` gate rides on the parametrization.
    """
    note = unique_marker("note")
    identity = await cell.provision(label=f"rejoin-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-recall-rejoin-{cell.adapter_id}", participants=[identity.id]
    )

    # Run 1: state the note, then stop the agent (exit the run context).
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                REMEMBER.format(note=note),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(mid, identity.id)

    # Run 2: a brand-new adapter under the SAME identity — no in-memory history,
    # so a correct recall proves the platform rehydrated the room on bootstrap.
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()  # scope to the recall turn
            mid = await user_ops.send_message(
                room_id,
                RECALL,
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(mid, identity.id, since=mark)
            replies.assert_contains_any([note])
