"""Foreign-framework context recall scenario shared across execution lanes."""

from __future__ import annotations

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import REPLY_PROMPT, unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    AdapterCell,
    ProvisionedAgent,
    ResourceManager,
    agent_rest_client,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Never names the token, so A can only answer from rehydrated context.
RECALL_TOKEN_REQUEST = (
    "Earlier the other participant sent you a short note with a token. "
    "Call band_send_message to reply with exactly that complete token string "
    "and nothing else."
)


def _relay_prompt(target: ProvisionedAgent, marker: str) -> str:
    """A prompt that drives B to send one message mentioning A and carrying the marker."""
    return (
        REPLY_PROMPT + f" When asked, send exactly one message that mentions the "
        f"participant '{target.name}' and contains this exact token: {marker}. Address "
        "it to them and include nothing else of substance."
    )


async def rehydrate_foreign_peer(
    cell: AdapterCell,
    peer: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
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
    async with (
        peer.run_as(speaker, prompt=_relay_prompt(recaller, marker)),
        reply_capture(room_id) as capture,
    ):
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

    context = await agent_rest_client(
        recaller, baseline_settings
    ).agent_api_context.get_agent_chat_context(chat_id=room_id)
    assert any(marker in (item.content or "") for item in context.data or []), (
        f"agent-scoped bootstrap context lost the exact foreign token {marker!r}"
    )

    # A boots fresh under its own identity — no in-memory history — and is asked what the
    # other participant told it. A correct recall can only come from the platform
    # rehydrating B's (foreign-framework) message into A's context on bootstrap.
    async with cell.run_as(recaller), reply_capture(room_id) as capture:
        mark = capture.messages.snapshot()  # scope strictly to the recall turn
        mid = await user_ops.send_message(
            room_id,
            RECALL_TOKEN_REQUEST,
            mention_id=recaller.id,
            mention_name=recaller.name,
        )
        replies = await capture.wait_for_reply(mid, recaller.id, since=mark)
        replies.assert_contains_exact(marker)
