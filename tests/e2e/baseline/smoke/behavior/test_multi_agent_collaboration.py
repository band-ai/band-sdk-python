"""Multi-agent collaboration smokes: a room of *different framework types* doing
different things together.

Three scenarios, each a single shared room with a heterogeneous cast built from
the registry's tool-capable, ``core``-lane adapters (anthropic + pydantic_ai +
agno) so they all install in one venv and the run is lane-schedulable:

* ``test_coordinator_delegates_to_two_specialists`` — a coordinator fans a
  compound request out to two specialists (one per framework), each runs its own
  tool, and the coordinator synthesizes both opaque results.
* ``test_coordinator_recruits_specialist_mid_conversation`` — a room that starts
  with the coordinator alone; it uses ``band_add_participant`` to recruit a
  specialist mid-conversation, which then runs the tool the coordinator can't.
* ``test_heterogeneous_agents_triage_concurrent_mentions`` — three framework
  types in one room, hit with concurrent mentions, each handling only its own.

Design notes (why this shape, not a bespoke build):

- **One uniform prompt, roles via the user message.** ``@with_adapters`` builds
  every agent with the same prompt/tools, so the *role* (coordinator vs.
  specialist) is set by the runtime-injected user message — exactly how the
  greeting smoke injects peer names. This keeps the test on the lane-safe
  decorator path (gated, provisioned, reaped, and ``BAND_E2E_LANE``-scoped) that a
  bespoke ``build_adapter`` call would bypass.
- **Delegation integrity comes from sender-scoped tool reads, not tool absence.**
  Every agent carries both tools, so the coordinator *could* answer alone; we
  prove it didn't by asserting the *specialist's own* ``tool_call`` events fired
  (``tool_calls(sender_id=specialist)``). The values are opaque (a secret code, a
  fictional forecast) — they can only reach the room if the specialist actually
  ran its tool.
- **Cascade barrier via ``wait_until``.** A coordinator→specialists→coordinator
  cascade spans several turns, so a single ``wait_for_processed`` won't cover it;
  we barrier on the opaque results appearing in the room (the only race-free
  "the cascade finished" signal that needs no live tool subscription). The
  concurrent-triage test has no cascade, so it uses the per-mention delivery
  barrier.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.baseline.agents import Adapter, with_adapters
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_tools import (
    ACCESS_CODES,
    EXECUTION_REPORTING,
    FORECASTS,
    LOOKUP,
    LOOKUP_TOOL,
    WEATHER,
    WEATHER_TOOL,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.observations import Replies
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The tool-capable, single-lane (``core``) cast. anthropic + pydantic_ai + agno are
# three genuinely different frameworks that all translate a ``ToolSpec`` and all
# install under the ``dev`` extra, so the heterogeneous room is lane-schedulable.
PANEL = (Adapter.ANTHROPIC, Adapter.PYDANTIC_AI, Adapter.AGNO)

# A dual-mode prompt: the same text serves a specialist (answer with the matching
# tool) and a coordinator (delegate, don't answer). Which branch applies is set by
# the user message each agent receives, so one uniform ``@with_adapters`` prompt
# covers every role.
COLLAB_PROMPT = (
    "You are one agent in a shared multi-agent room. You have two tools for values "
    f"you cannot know on your own: `{LOOKUP}` returns a secret access code for a "
    f"key, and `{WEATHER}` returns the forecast for a place. Follow the user's "
    "instructions exactly.\n"
    "- If you are asked to obtain a code or forecast, you MUST call the matching "
    "tool (you cannot guess the value), then report the result in one short "
    "band_send_message that mentions whoever asked.\n"
    "- If you are instead asked to COORDINATE other named agents, do NOT call the "
    "tools yourself: send a band_send_message mentioning each named agent with the "
    "specific request, wait for their replies, then send a final band_send_message "
    "to the user containing every result you received."
)

# Opaque targets, referenced from the source maps so a change there surfaces here.
PANEL_KEY = "gamma"  # ACCESS_CODES["gamma"] -> a code the model can't guess
PANEL_PLACE = "Qyx"  # FORECASTS["qyx"]      -> a forecast the model can't guess
RECRUIT_KEY = "beta"
# A stable fragment of the opaque forecast; asserted against the source so a reword
# in sample_tools fails loudly here instead of silently weakening the barrier.
FORECAST_FRAGMENT = "triple sunrise"
assert FORECAST_FRAGMENT in FORECASTS[PANEL_PLACE.lower()]


@with_adapters(
    *PANEL,
    tools=[LOOKUP_TOOL, WEATHER_TOOL],
    prompt=COLLAB_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.flaky(reruns=2)  # a multi-hop cascade occasionally drops a turn; retry
@pytest.mark.timeout(extra=300)  # cascade spans several live turns
@pytest.mark.asyncio(loop_scope="session")
async def test_coordinator_delegates_to_two_specialists(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A coordinator delegates a compound task to two specialists of two frameworks.

    The room holds three framework types. The user mentions only the coordinator
    and asks it to gather an access code from one specialist and a forecast from
    the other — so each specialist runs a *different* tool, and the only way both
    opaque results can reach the room is if the coordinator delegated to both.

    Delegation (not the coordinator's optional final synthesis) is the contract
    asserted here: the coordinator *mentioned both specialists* (the user never
    did), each specialist *ran its own tool*, and *both opaque results landed*.
    The coordinator's re-synthesis turn is a flaky second hop, so we don't gate on
    it.
    """
    coordinator, lookup_spec, weather_spec = agents
    room_id = await resource_manager.provision_room(
        title="e2e-specialist-panel",
        participants=[coordinator.id, lookup_spec.id, weather_spec.id],
    )
    # The cascade is coordinator -> 2 specialists, so budget a few turns off the
    # single-source per-turn timeout (never a magic literal).
    cascade_deadline = baseline_settings.e2e_timeout * 3

    async with reply_capture(room_id) as capture:
        await user_ops.send_message(
            room_id,
            (
                "Coordinate your two specialists to answer me — do not look "
                f"anything up yourself. Ask {lookup_spec.name} for the access code "
                f"for key '{PANEL_KEY}', and ask {weather_spec.name} for the "
                f"forecast for {PANEL_PLACE}. Once you have BOTH of their replies, "
                "send me one message containing the access code and the forecast."
            ),
            mention_id=coordinator.id,
            mention_name=coordinator.name,
        )

        # Barrier on both opaque results reaching the room: they can only appear
        # once each specialist has actually run its tool and reported back, which
        # is the race-free "the delegation completed" signal.
        def both_results_in(messages: list) -> bool:
            text = " ".join(m.content.lower() for m in messages)
            return ACCESS_CODES[PANEL_KEY].lower() in text and FORECAST_FRAGMENT in text

        await capture.wait_until(both_results_in, deadline_s=cascade_deadline)

        lookup_calls, weather_calls = await asyncio.gather(
            capture.tool_calls(sender_id=lookup_spec.id),
            capture.tool_calls(sender_id=weather_spec.id),
        )
        coordinator_msgs = Replies(
            m for m in capture.messages if m.sender_id == coordinator.id
        )

    # The coordinator (not the user, who only mentioned the coordinator) delegated
    # to BOTH specialists — proof it did the coordinating.
    coordinator_msgs.assert_mentions(lookup_spec.id)
    coordinator_msgs.assert_mentions(weather_spec.id)
    # Each specialist ran ITS OWN tool — proof the panel collaborated rather than
    # the coordinator answering alone (the opaque values can't be guessed).
    lookup_calls.assert_fired(LOOKUP, with_args={"key": PANEL_KEY})
    weather_calls.assert_fired(WEATHER, with_args={"place": PANEL_PLACE})
    # Both opaque results made it back into the shared room.
    capture.messages.assert_contains_any([ACCESS_CODES[PANEL_KEY]])
    capture.messages.assert_contains_any([FORECAST_FRAGMENT])


@with_adapters(
    Adapter.ANTHROPIC,
    Adapter.PYDANTIC_AI,
    tools=[LOOKUP_TOOL],
    prompt=COLLAB_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.flaky(reruns=2)  # recruit + cascade occasionally drops a turn; retry
@pytest.mark.timeout(extra=300)  # recruitment + a delegation round-trip
@pytest.mark.asyncio(loop_scope="session")
async def test_coordinator_recruits_specialist_mid_conversation(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    baseline_settings: BaselineSettings,
) -> None:
    """A coordinator alone in a room recruits a specialist to do what it can't.

    The room starts with only the coordinator; the specialist is running but
    out-of-room. The coordinator must use ``band_add_participant`` (a platform
    tool) to bring the specialist in, then delegate the lookup — exercising the
    participant-management tools end to end across two framework types.
    """
    coordinator, specialist = agents
    # Room starts with ONLY the coordinator — the specialist must be recruited in.
    room_id = await resource_manager.provision_room(
        title="e2e-dynamic-recruitment", participants=[coordinator.id]
    )
    cascade_deadline = baseline_settings.e2e_timeout * 3

    async with reply_capture(room_id) as capture:
        await user_ops.send_message(
            room_id,
            (
                f"I need the secret access code for key '{RECRUIT_KEY}'. Do not "
                f"look it up yourself. There is a specialist agent named "
                f"{specialist.name} (id {specialist.id}) who can. Use "
                "band_add_participant to add them to this room, then ask them for "
                "the code, and report it back to me."
            ),
            mention_id=coordinator.id,
            mention_name=coordinator.name,
        )

        # Barrier on the recruited specialist's opaque result reaching the room.
        def code_in_room(messages: list) -> bool:
            return any(
                ACCESS_CODES[RECRUIT_KEY].lower() in m.content.lower() for m in messages
            )

        await capture.wait_until(code_in_room, deadline_s=cascade_deadline)

        specialist_calls = await capture.tool_calls(sender_id=specialist.id)
        participant_ids = await user_ops.list_participant_ids(room_id)

    # Recruitment happened: the specialist is now a participant of the room...
    assert specialist.id in participant_ids, (
        f"expected {specialist.name} ({specialist.id}) to have been recruited "
        f"into the room, but participants are {participant_ids}"
    )
    # ...and it ran the lookup tool the coordinator was told not to run itself.
    specialist_calls.assert_fired(LOOKUP, with_args={"key": RECRUIT_KEY})
    # The recruited specialist's opaque result reached the room.
    capture.messages.assert_contains_any([ACCESS_CODES[RECRUIT_KEY]])


@with_adapters(
    *PANEL,
    tools=[LOOKUP_TOOL, WEATHER_TOOL],
    prompt=COLLAB_PROMPT,
    **EXECUTION_REPORTING,
)
@pytest.mark.flaky(
    reruns=2, rerun_except=["AssertionError"]
)  # retry a transient live-turn timeout; assertion failures fail loud
@pytest.mark.timeout(extra=180)  # three concurrent turns
@pytest.mark.asyncio(loop_scope="session")
async def test_heterogeneous_agents_triage_concurrent_mentions(
    agents: list[ProvisionedAgent],
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """Three framework types in one room each handle only their own mention.

    The user fires three mentions concurrently, one per agent, each a different
    task. Per-mention delivery barriers settle every turn; sender-scoped tool
    reads then prove each agent ran only its own tool with its own argument — no
    cross-talk under concurrent load.
    """
    anthropic_agent, pydantic_agent, agno_agent = agents
    room_id = await resource_manager.provision_room(
        title="e2e-concurrent-triage", participants=[a.id for a in agents]
    )

    async with reply_capture(room_id) as capture:
        # Subscribe-before-send is already satisfied by the open capture; fire all
        # three mentions at once so the room is genuinely concurrent.
        m_anthropic, m_pydantic, m_agno = await asyncio.gather(
            user_ops.send_message(
                room_id,
                "look up the access code for key 'alpha'",
                mention_id=anthropic_agent.id,
                mention_name=anthropic_agent.name,
            ),
            user_ops.send_message(
                room_id,
                "get the weather for Zorath",
                mention_id=pydantic_agent.id,
                mention_name=pydantic_agent.name,
            ),
            user_ops.send_message(
                room_id,
                "look up the access code for key 'gamma'",
                mention_id=agno_agent.id,
                mention_name=agno_agent.name,
            ),
        )
        await asyncio.gather(
            capture.wait_for_processed(m_anthropic, anthropic_agent.id),
            capture.wait_for_processed(m_pydantic, pydantic_agent.id),
            capture.wait_for_processed(m_agno, agno_agent.id),
        )
        anthropic_calls, pydantic_calls, agno_calls = await asyncio.gather(
            capture.tool_calls(sender_id=anthropic_agent.id),
            capture.tool_calls(sender_id=pydantic_agent.id),
            capture.tool_calls(sender_id=agno_agent.id),
        )

    # Each agent answered only its own mention, under concurrent load.
    anthropic_calls.assert_fired(LOOKUP, with_args={"key": "alpha"})
    assert not any(c.args.get("key") == "gamma" for c in anthropic_calls), (
        "anthropic agent answered another agent's lookup mention"
    )
    pydantic_calls.assert_fired(WEATHER, with_args={"place": "Zorath"})
    assert not pydantic_calls.fired(LOOKUP), (
        "pydantic_ai agent ran a lookup it was never asked for"
    )
    agno_calls.assert_fired(LOOKUP, with_args={"key": "gamma"})
    assert not any(c.args.get("key") == "alpha" for c in agno_calls), (
        "agno agent answered another agent's lookup mention"
    )
