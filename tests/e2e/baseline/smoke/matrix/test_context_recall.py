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

Replaces the now-removed legacy ``tests/e2e/scenarios/test_context_persistence.py``
(the rejoin case), on the baseline toolkit; the in-session case is the simpler
sibling. The rejoin lifecycle uses ``running_agent`` — the run half factored out
of ``running_provisioned_agent`` — entered twice against one provisioned identity.

Wording note: a neutral "note", not a "secret code" (models refuse to echo a
credential-shaped value — an unrelated false failure).
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.agents import across_adapters
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.adapters import build_adapter
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# A reply-oriented prompt shared by both variants so the comparison is fair: the
# agent answers in chat, acknowledging when told to remember and stating the value
# when later asked. (The default matrix prompt is tool-oriented.)
REPLY_PROMPT = (
    "You are a helpful assistant in a chat room. Reply directly with one short "
    "sentence. When asked to remember something, acknowledge it; when later asked "
    "what it was, state it exactly."
)
REMEMBER = "Please remember this note: {note}. Confirm you remember it."
RECALL = "What was the note I asked you to remember? Reply with just it."


@across_adapters(prompt=REPLY_PROMPT)
@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=120)  # two turns (state, then recall)
@pytest.mark.asyncio(loop_scope="session")
async def test_recalls_within_session(
    adapter_id: str,
    matrix_agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Turn 2 recalls a note stated in turn 1 (in-session history conversion)."""
    note = unique_marker("note")
    room_id = await resource_manager.provision_room(
        title=f"e2e-recall-session-{adapter_id}", participants=[matrix_agent.id]
    )
    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            REMEMBER.format(note=note),
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(mid, matrix_agent.id)

        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            RECALL,
            mention_id=matrix_agent.id,
            mention_name=matrix_agent.name,
        )
        await capture.wait_for_processed(mid, matrix_agent.id)
        capture.messages.since(mark).assert_contains_any([note])


@pytest.mark.flaky(reruns=2, rerun_except=["AssertionError"])  # only transient failures
@pytest.mark.timeout(extra=180)  # two agent startups (state, then rejoin)
@pytest.mark.asyncio(loop_scope="session")
async def test_recalls_after_rejoin(
    adapter_id: str,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A fresh adapter under the same identity recalls via platform rehydration.

    Requests the parametrized ``adapter_id`` fixture (not ``matrix_agent``) so the
    test owns the agent lifecycle: it provisions the identity once, then runs a
    fresh adapter twice via ``running_agent`` — a stop→rejoin. The per-cell
    ``@requires`` gate rides on the ``adapter_id`` params.
    """
    note = unique_marker("note")
    agent = await resource_manager.provision_agent(f"rejoin-{adapter_id}")
    room_id = await resource_manager.provision_room(
        title=f"e2e-recall-rejoin-{adapter_id}", participants=[agent.id]
    )

    # Run 1: state the note, then stop the agent (exit the run context).
    adapter = build_adapter(adapter_id, baseline_settings, prompt=REPLY_PROMPT)
    async with running_agent(agent, adapter, baseline_settings):
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id,
                REMEMBER.format(note=note),
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)

    # Run 2: a brand-new adapter under the SAME identity — no in-memory history,
    # so a correct recall proves the platform rehydrated the room on bootstrap.
    adapter = build_adapter(adapter_id, baseline_settings, prompt=REPLY_PROMPT)
    async with running_agent(agent, adapter, baseline_settings):
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()  # scope to the recall turn
            mid = await user_ops.send_message(
                room_id,
                RECALL,
                mention_id=agent.id,
                mention_name=agent.name,
            )
            await capture.wait_for_processed(mid, agent.id)
            capture.messages.since(mark).assert_contains_any([note])
