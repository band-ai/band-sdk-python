"""Matrix scenario: cold-boot recall of a fact posted while the agent was offline.

A stricter sibling of ``test_recalls_after_rejoin`` in ``test_context_recall``.
That test states the note while the agent is *up* in run 1 — the adapter sees it
live — and only checks recall survives a reboot. Here the note is posted into the
room while the identity has **no running adapter at all**, so the adapter never
sees the message live. On its one and only (cold) boot, the sole path to a correct
recall is the platform rehydrating the room's history via ``/context`` on
bootstrap; there is no in-memory transcript it could have retained.

Posting the note is a bare ``user_ops.send_message`` — deliberately *not* wrapped
in ``cell.run_as`` and *not* barriered (the agent is down, so the message is never
processed). The REST call returning is the deterministic guarantee that the
message is persisted server-side and will appear in ``/context`` on boot. On boot
the agent may also process the backlog mention; that is harmless — ``since(mark)``
scopes the assertion to the recall turn, so either way the recall reply must carry
the note.

Excludes ``codex`` / ``opencode``: those adapters recover context on reboot by
resuming their own backend session (a session id persisted via task events), not by
consuming platform ``/context`` as history — a different mechanism, so a pass there
would not validate the ``/context`` rehydration this scenario asserts.

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
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(exclude={Adapter.CODEX, Adapter.OPENCODE}, prompt=REPLY_PROMPT)
# Cold-boot recall is model-non-deterministic: a capable model occasionally answers
# "I don't have access to a stored note" despite the note being present in the
# rehydrated context (byte-level confirmed for pydantic_ai; ~2/4). That intermittent
# recall miss is a model flake, not a code defect, so — unlike the matrix's usual
# rerun_except=["AssertionError"] — allow AssertionError reruns here. A real
# rehydration regression fails deterministically and stays red across all reruns.
@pytest.mark.flaky(reruns=2)
@pytest.mark.timeout(extra=180)  # cold boot + backlog + recall
@pytest.mark.asyncio(loop_scope="session")
async def test_recalls_offline_note_on_cold_boot(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A cold-booted adapter recalls a note posted while it had no running adapter.

    Requests ``cell`` so the test owns the lifecycle: it provisions the identity and
    posts the note into the room with no adapter running (never seen live), then runs
    a fresh adapter exactly once. A correct recall can only come from the platform
    rehydrating the room on bootstrap (``/context``). The prompt set on
    ``@per_adapter`` is carried by the cell, so the run needs no per-call steering.
    """
    note = unique_marker("note")
    identity = await cell.provision(label=f"offline-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-rehydrate-offline-{cell.adapter_id}", participants=[identity.id]
    )

    # Post the note while NO adapter is running for this identity: never barriered
    # (the agent is down), so it is never seen live. The REST call returning means
    # it is persisted server-side and will appear in /context on boot.
    await user_ops.send_message(
        room_id,
        REMEMBER.format(note=note),
        mention_id=identity.id,
        mention_name=identity.name,
    )

    # First and only run: a brand-new adapter under this identity — no in-memory
    # history, so a correct recall proves the platform rehydrated the room on boot.
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()  # scope to the recall turn
            mid = await user_ops.send_message(
                room_id,
                RECALL,
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(mid, identity.id)
            capture.messages.since(mark).assert_contains_any([note])
