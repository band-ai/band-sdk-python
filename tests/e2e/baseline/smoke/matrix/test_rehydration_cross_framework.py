"""Matrix scenario: a *different-framework* peer's message rehydrates into A's context.

Existing rehydration/recall tests have the **user** author the recalled fact
(``test_context_recall``, ``test_rehydration_offline``) or recall a user-authored note
alongside a live peer (``test_rehydration_partial``); none proves that one framework
rehydrates a message a *different* framework authored. Here
peer B (langgraph, an OpenAI-backed LangChain agent) authors a message — carrying a
neutral marker and mentioning A — while agent A (each of the other core frameworks) is
offline; then A cold-boots and must recall the marker from its bootstrap ``/context``.

Why B must mention A — and why this is *text*, not a tool call: rehydrated ``/context``
is **agent-scoped** — the platform returns only messages the agent authored or was
*mentioned in*, not the whole room (see ``execution.py`` ``get_agent_chat_context`` +
``preprocessing/default._load_history``). So B's message only reaches A's context if it
mentions A. A tool call/result B makes is B's own and mentions no one, so it can *never*
enter A's context — cross-framework **tool-event** rehydration is therefore not
observable through ``/context`` and is a deliberate non-goal here.

What this proves: a message **authored by a different framework** rehydrates into A's
agent-scoped context and A recalls it. Message text is framework-neutral on the platform,
so this is a deliberately narrow, honest claim: the cross-framework analogue of the
same-framework ``test_rehydration_offline`` / ``test_rehydration_partial`` recall.

Leak-avoidance / fail-loud setup: the marker is a neutral ``unique_marker`` (not a
credential-shaped token models refuse to echo). B is driven to send exactly one message
that mentions A and carries the marker; a **setup precondition** asserts that message
actually mentioned A (by metadata) and carried the marker — so a peer that failed to
mention A is a loud setup red, not a silent "A couldn't recall" that looks like a
rehydration bug.

Lifecycle: A's identity via ``cell`` (owns its boot); B's via the ``peer`` fixture (a
different-framework cell the test drives). The two runs are strictly sequential — B's
run fully exits before A boots — so ``track_running`` never sees overlapping runs of one
identity. ``lane=Lane.CORE`` + ``exclude={LANGGRAPH}`` keeps A and B both in the core
lane (schedulable, no cross-lane) and guarantees A is never langgraph (so A ≠ B).
"""

from __future__ import annotations

import pytest
from tests.e2e.baseline.flaky import flaky_infra

from tests.e2e.baseline.agents import Adapter, Lane, per_adapter
from tests.e2e.baseline.smoke.samples.sample_agents import REPLY_PROMPT, unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps


def _relay_prompt(target: ProvisionedAgent, marker: str) -> str:
    """A prompt that drives B to send one message mentioning A and carrying the marker."""
    return (
        REPLY_PROMPT + f" When asked, send exactly one message that mentions the "
        f"participant '{target.name}' and contains this exact token: {marker}. Address "
        "it to them and include nothing else of substance."
    )


@per_adapter(
    lane=Lane.CORE,
    exclude={Adapter.LANGGRAPH},
    peer=Adapter.LANGGRAPH,
    prompt=REPLY_PROMPT,
)
@flaky_infra("only transient failures")
@pytest.mark.timeout(extra=300)  # peer boot + relay turn + fresh A boot + recall turn
@pytest.mark.asyncio(loop_scope="session")
async def test_rehydrates_foreign_peer_message(
    cell: AdapterCell,
    peer: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A recalls a marker a *different-framework* peer stated, via agent-scoped ``/context``.

    Requests ``cell`` (A's lifecycle) and ``peer`` (B, a different framework). B authors a
    message mentioning A that carries the marker (so it enters A's agent-scoped context),
    then stops; A cold-boots and must recall the marker from bootstrap rehydration. The
    per-cell ``@requires`` gate (folded with the peer's) rides on the parametrization.
    """
    marker = unique_marker("note")
    recaller = await cell.provision(label=f"recaller-{cell.adapter_id}")
    speaker = await peer.provision(label=f"speaker-{peer.adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-xframework-{cell.adapter_id}",
        participants=[recaller.id, speaker.id],
    )

    # B (a different framework) authors one message that mentions A and carries the
    # marker, then stops. Mentioning A is what lands it in A's agent-scoped /context.
    async with peer.run_as(speaker, prompt=_relay_prompt(recaller, marker)):
        async with reply_capture(room_id) as capture:
            probe = capture.messages.snapshot()
            mid = await user_ops.send_message(
                room_id,
                f"Please pass a note to {recaller.name}.",
                mention_id=speaker.id,
                mention_name=speaker.name,
            )
            replies = await capture.wait_for_reply(mid, speaker.id, since=probe)
            # Setup precondition (fail loud): B's marker-bearing message must actually
            # mention A — else it never reaches A's context and a later recall miss would
            # look like a rehydration bug rather than a setup failure.
            replies.mentioning(recaller.id).assert_contains_any([marker])

    # A boots fresh under its own identity — no in-memory history — and is asked what the
    # other participant told it. A correct recall can only come from the platform
    # rehydrating B's (foreign-framework) message into A's context on bootstrap.
    async with cell.run_as(recaller):
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()  # scope strictly to the recall turn
            mid = await user_ops.send_message(
                room_id,
                "Earlier the other participant sent you a short note with a token. "
                "Reply with just that token.",
                mention_id=recaller.id,
                mention_name=recaller.name,
            )
            replies = await capture.wait_for_reply(mid, recaller.id, since=mark)
            replies.assert_contains_any([marker])
