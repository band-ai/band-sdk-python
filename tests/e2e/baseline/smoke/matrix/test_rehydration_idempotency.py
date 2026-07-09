"""Matrix scenario: a cold restart is idempotent, and its usage splits replay vs inference.

Two tests over the ``cell.run_as``-twice stop/cold-restart lifecycle (as proven by
``test_recalls_after_rejoin`` / ``test_rehydration_partial``). On cold boot the SDK drains
the server ``/next`` unprocessed queue before the WS queue, and ``mark_processed`` removes
handled messages from it — so an offline message is answered on boot with no new trigger,
while an already-handled message is *not* re-drained (excluded from ``/next``). "Completed"
is load-bearing: ``mark_processed`` runs only after the *full* turn (tool loop included),
so this needs a clean stop — the toolkit's ``run_as`` exit, not an abrupt kill (a
mid-tool-call crash is re-run by design, out of baseline scope).

Dedup is asserted at the **delivery layer**, not via reply content. A live run showed
why: rehydration re-sends the room history to the model, and a weak model re-mentions —
even re-executes (re-invokes ``band_add_participant``, re-emits a prior token) — the
historical instructions within the new turn. So "the marker/participant reappeared" does
NOT imply the message was re-processed; the message-level dedup still holds (only the one
genuinely-unprocessed offline message is drained). The sound, model-independent signal is
that the already-handled messages get no fresh ``PROCESSING`` transition on boot; offline
pickup + history-restore ride a positive recall check. (The re-execution behaviour itself
is tracked separately as a possible rehydration/converter concern.)

Subtlety — the run-2 ``reply_capture`` is opened *before* ``run_as`` enters: the
boot-drain answers the offline question during startup, so the observer must already be
subscribed or the reply races past it (the boot-drain analogue of subscribe-before-send).

Two tests, so crewai (whose usage is cumulative, not per-turn) keeps the dedup coverage
while only the token-split test excludes it. ``runs_tool_loop=True`` already excludes
codex/opencode/crewai_flow (the session-resuming backends), subsuming the explicit
exclusion the other rehydration tests spell out.
"""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import (
    RECALL,
    REMEMBER,
    REPLY_PROMPT,
    invite_instruction,
    liveness_probe,
    unique_marker,
    usage_features,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps


@per_adapter(runs_tool_loop=True, prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2)  # cold-boot recall is model-non-deterministic
@pytest.mark.timeout(extra=300)  # several run-1 turns + two agent boots
@pytest.mark.asyncio(loop_scope="session")
async def test_handled_work_not_redrained_on_restart(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Cold restart: history restored, offline picked up, and already-handled messages
    are not re-drained from /next (the model-independent dedup signal)."""
    note = unique_marker("note")
    handled = unique_marker("handled")
    echo = await resource_manager.provision_agent("echo")
    identity = await cell.provision(label=f"idem-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-idempotency-{cell.adapter_id}", participants=[identity.id]
    )

    # Run 1: state a note, invite Echo (a completed band_add_participant), answer a
    # marked probe — then stop cleanly so mark_processed runs for every turn.
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            note_mid = await user_ops.send_message(
                room_id,
                REMEMBER.format(note=note),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(note_mid, identity.id)
            invite_mid = await user_ops.send_message(
                room_id,
                invite_instruction(echo.name, echo.id),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            await capture.wait_for_processed(invite_mid, identity.id)
            handled_mid = await user_ops.send_message(
                room_id,
                liveness_probe(handled),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            # Pre-restart: the marked probe was answered.
            replies = await capture.wait_for_reply(
                handled_mid, identity.id, sender_id=identity.id
            )
            replies.assert_contains_any([handled])
        # ...and the completed invite put Echo in the room.
        after_invite = await user_ops.list_participant_ids(room_id)
        assert echo.id in after_invite, (
            f"expected {echo.name} invited into the room; participants: {after_invite}"
        )

    # Between runs (agent down): queue an offline recall so the boot-drain has exactly
    # one genuinely-unprocessed message to answer.
    offline_mid = await user_ops.send_message(
        room_id, RECALL, mention_id=identity.id, mention_name=identity.name
    )

    # Run 2 (cold): subscribe BEFORE booting — the boot-drain answers the offline
    # question during startup, before any post-boot trigger.
    async with reply_capture(room_id) as capture:
        async with cell.run_as(identity):
            # Wait for the boot-drain reply itself (asserted after the run closes).
            replies = await capture.wait_for_reply(
                offline_mid, identity.id, sender_id=identity.id
            )
        # The capture opened BEFORE the cold boot and the offline barrier just succeeded,
        # so it observed every room delivery event from boot onward — including, had the
        # SDK re-drained an already-handled message, that message's fresh PROCESSING. So a
        # run-1 message with no PROCESSING transition here means "not re-drained", not
        # "the capture saw nothing" — the offline barrier is the positive control.
        redrained = {
            mid: capture.delivery_history(mid, identity.id)
            for mid in (note_mid, invite_mid, handled_mid)
        }

    # Offline pickup + history restored: the boot-drain recalled the note.
    replies.assert_contains_any([note])
    # Message-level dedup (model-independent): none of the already-handled run-1 messages
    # was re-drained from /next on cold boot, so none shows a fresh PROCESSING transition.
    # Content is deliberately NOT used — rehydration lets a weak model re-mention or even
    # re-execute history, which the /next dedup does not (the offline message is the only
    # thing drained). This one check subsumes "already-handled message not re-answered"
    # and "completed tool call not re-run": their triggering messages stay drained.
    for mid, history in redrained.items():
        assert DeliveryStatus.PROCESSING not in history, (
            f"already-handled message {mid} was re-processed on cold boot: {history}"
        )


@per_adapter(
    runs_tool_loop=True,
    exclude={Adapter.CREWAI},  # cumulative usage → the per-turn token gate is N-A
    prompt=REPLY_PROMPT,
    features=usage_features(),
)
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient reruns
@pytest.mark.timeout(extra=300)  # two agent boots + two turns
@pytest.mark.asyncio(loop_scope="session")
async def test_restart_usage_splits_replay_and_inference(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """The post-restart turn's usage shows replayed input AND fresh-inference output.

    crewai is excluded (its usage counter is cumulative-lifetime, not per-turn, so the
    gate is N-A); its rehydration recall is covered by ``test_recalls_after_rejoin``.
    """
    note = unique_marker("note")
    identity = await cell.provision(label=f"usage-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-restart-usage-{cell.adapter_id}", participants=[identity.id]
    )

    # Run 1: state a note; the boundary is the server timestamp of its reply, used to
    # scope the run-2 usage read so run-1's own usage record is excluded.
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                REMEMBER.format(note=note),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            # Wait for the reply so turn_boundary() has a timestamp to read.
            await capture.wait_for_reply(mid, identity.id, sender_id=identity.id)
            boundary = capture.turn_boundary()

    # Run 2 (cold): a post-boot recall turn. Non-zero input tokens mean the rehydrated
    # /context was re-sent to the model (replay); non-zero output means a fresh reply
    # (new inference) — the L4 token gate, scoped past the run-1 boundary.
    async with cell.run_as(identity):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, RECALL, mention_id=identity.id, mention_name=identity.name
            )
            await capture.wait_for_processed(mid, identity.id)
            usage = await capture.usage(sender_id=identity.id, since=boundary)

    usage.assert_nonzero_input_and_output()
