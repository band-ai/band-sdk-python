"""Matrix scenario: sender identity survives a reboot's history rehydration.

After an agent is stopped and a *fresh* adapter is started under the same
identity, the platform rehydrates the room's history on bootstrap (``/context``).
This proves that rehydrated history preserves *who said what*: the rebooted agent
can attribute two facts to their correct sources — one stated by the user, one
stated by a peer agent.

Why the existing recall/rejoin tests don't cover this: ``test_context_recall``'s
rejoin case asserts only that a fact's *content* resurfaces after reboot, and
every message in it comes from a single sender (the user). It says nothing about
whether the rehydrated transcript still carries per-message *sender identity*. A
converter could rebuild all history as anonymous "user" turns and still pass that
test. Here the room has two distinct authors, so the only way to answer "who
stated each code?" correctly is if sender attribution survived ``/context``.

Leak-avoidance is load-bearing here — the test is only meaningful if the agent
*cannot* answer from anything but rehydrated sender identity. Three things keep it
honest, and must stay that way if this test is edited:

* **Neutral, symmetric facts.** Both facts are opaque "reference codes" with
  neutral markers (``alpha`` / ``bravo``) — no category (a "favorite color" vs a
  "token") that carries a prior about who would plausibly have said it, and no
  marker prefix that encodes its own source.
* **The peer authors its code itself.** The speaker runs under a custom prompt
  that assigns it the code; the user only *greets* it (never quoting the code). So
  ``peer_fact`` enters the room solely as the speaker's own message — if the user
  had quoted it, the user would be an author of it too, muddying attribution.
* **The question names neither the source nor which-code-is-which.** It asks the
  agent to repeat each code and name its author, forcing value→sender recovery
  from history rather than parroting the question. The judge criteria bind each
  code to its sender so a swapped or missing attribution fails.

The reboot lifecycle uses ``cell.run_as`` entered twice (stop→reboot) on one
provisioned identity; a second same-adapter identity plays the peer agent, up
during setup only.

Excludes ``codex`` / ``opencode``: those adapters recover context on reboot by
resuming their own backend session (a session id persisted via task events), not by
consuming platform ``/context`` as history — a different mechanism, so a pass there
would not validate the ``/context`` attribution fidelity this scenario asserts.
"""

from __future__ import annotations

import pytest

from collections.abc import Awaitable, Callable

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import REPLY_PROMPT, unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.judge import Verdict, format_transcript
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

JudgeFn = Callable[..., Awaitable[Verdict]]


@per_adapter(exclude={Adapter.CODEX, Adapter.OPENCODE}, prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=300)  # setup turns + peer boot + reboot + attribution turn
@pytest.mark.asyncio(loop_scope="session")
async def test_rehydrated_history_preserves_sender_attribution(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    judge: JudgeFn,
) -> None:
    """A rebooted agent attributes two codes to the right sources via ``/context``.

    Requests ``cell`` so the test owns the lifecycle. Two same-adapter identities are
    provisioned: the ``recaller`` (rebooted; must attribute) and a ``speaker`` peer
    (up during setup only, to author one of the codes under its own prompt). The
    recaller's two runs are strictly sequential — run 1 fully exits before run 2 — so
    ``track_running`` never sees overlapping runs of one identity. The prompt on
    ``@per_adapter`` rides on the cell; the per-cell ``@requires`` gate rides on the
    parametrization.
    """
    # Neutral, symmetric markers — no prefix that encodes the source (see module docstring).
    user_fact = unique_marker("alpha")
    peer_fact = unique_marker("bravo")

    recaller = await cell.provision(label=f"recaller-{cell.adapter_id}")
    speaker = await cell.provision(label=f"speaker-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-rehydrate-attribution-{cell.adapter_id}",
        participants=[recaller.id, speaker.id],
    )

    # The speaker runs under a custom prompt that assigns it peer_fact as its OWN
    # code, so peer_fact enters the room only as the speaker's message — never quoted
    # by the user. (If the user's message contained the token, the user would be an
    # author of it too, muddying the attribution the test checks.)
    speaker_prompt = (
        REPLY_PROMPT + f" You have been assigned the reference code {peer_fact}. "
        "When a participant greets you or asks you to introduce yourself, reply "
        "with exactly that code and nothing else."
    )

    # Run 1 (setup): both agents up. The user states one code to the recaller and
    # greets the speaker (without ever naming a code), so the room ends up with two
    # codes from two distinct senders. Then both agents stop (exit both contexts).
    async with cell.run_as(recaller):
        async with cell.run_as(speaker, prompt=speaker_prompt):
            async with reply_capture(room_id) as capture:
                mid = await user_ops.send_message(
                    room_id,
                    f"Please make a note of this reference code: {user_fact}. "
                    "Just acknowledge.",
                    mention_id=recaller.id,
                    mention_name=recaller.name,
                )
                await capture.wait_for_processed(mid, recaller.id)

                greet_mark = capture.messages.snapshot()
                mid = await user_ops.send_message(
                    room_id,
                    "Please introduce yourself with your assigned code.",
                    mention_id=speaker.id,
                    mention_name=speaker.name,
                )
                await capture.wait_for_processed(mid, speaker.id)

                # Precondition: the speaker actually emitted its code. If it didn't,
                # the scenario wasn't set up — a legitimate red (setup failure), not
                # flakiness to paper over.
                capture.messages.since(greet_mark).assert_contains_any([peer_fact])

    # Run 2 (reboot): a fresh adapter under the SAME recaller identity — no in-memory
    # history, so a correct attribution can only come from the platform rehydrating
    # the room on bootstrap. The question names neither author nor which code is
    # which, forcing value→sender recovery from history.
    async with cell.run_as(recaller):
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()  # scope strictly to the attribution turn
            mid = await user_ops.send_message(
                room_id,
                "Earlier in this room, two different participants each stated one "
                "short reference code. For EACH code, repeat the code exactly and "
                "name exactly who stated it — the user, or the other participant by "
                "their name. Use only what was actually said in this room; do not "
                "guess.",
                mention_id=recaller.id,
                mention_name=recaller.name,
            )
            await capture.wait_for_processed(mid, recaller.id)
            recall = capture.messages.since(mark)

    # Cheap structural pre-checks first: both codes must resurface after rehydration.
    recall.assert_contains_any([user_fact])
    recall.assert_contains_any([peer_fact])

    # Then the judge, for the one thing no substring check can express: that each
    # code is attributed to its correct sender. Bound value→sender so a swapped or
    # missing attribution fails.
    verdict = await judge(
        criteria=(
            "Two reference codes were stated by two different senders earlier in the "
            f"room: the USER stated '{user_fact}', and a peer agent (named "
            f"'{speaker.name}') stated '{peer_fact}'. Pass ONLY if the reply "
            f"attributes '{user_fact}' to the user (or 'you') AND attributes "
            f"'{peer_fact}' to the peer agent — by its name, or any unambiguous "
            "reference to the other participant that is clearly NOT the user. Fail "
            "if either code is unattributed, attributed to the wrong sender, or the "
            "two attributions are swapped."
        ),
        transcript=recall,
    )
    assert verdict.passed, f"{verdict.reasoning}\n{format_transcript(recall)}"
