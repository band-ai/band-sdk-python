"""Live smoke coverage for the outbound ACP room-visible tool contract.

The scenario is intentionally backend-neutral: it asks the ACP agent to emit one
Band event, then checks that the event was persisted and that the call was
narrated as an ordinary ACP tool call — like any other tool, with no special
suppression for Band messaging tools.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
from band.core.model_catalog import ModelSelection
from band.core.types import MessageType, metadata_to_dict
from tests.e2e.baseline.agents import Adapter, Lane, lane, with_adapters
from tests.e2e.baseline.flaky import flaky_model
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    TOOL_AGENT,
    emit_event_instruction,
    unique_marker,
)
from tests.e2e.baseline.toolkit.builders import copilot_acp_env, copilot_home_dir
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.observations.events import Tasks
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

# The Band platform tool the sample agent's instructions ask it to call (see
# emit_event_instruction). Test-local identity, not band.runtime.tools.EVENT_TOOL_NAMES
# -- that vocabulary answers a different question ("is this tool observational,
# not terminal work, for no-reply detection"), which only coincides with this one today.
BAND_EVENT_TOOL_NAME = "band_send_event"


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_call_is_narrated(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A band_send_event call is narrated as an ACP tool_call, like any other tool.

    Uses the raw ``events`` reader (not the JSON-based ``tool_calls`` helper):
    the room event's content is the serialized ``ToolCallRoomEvent`` wrapper
    (``name``/``args``/``tool_call_id``), and a plain substring check on the
    unescaped ``name`` field is enough to prove the call was narrated -- no
    need to decode it for that.
    """
    marker = unique_marker("acp-event")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-call-narrated", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_call_events = await capture.events(
            MessageType.TOOL_CALL, sender_id=agent.id
        )

    thoughts.assert_contains_any([marker])
    tool_call_events.assert_at_least(1)
    tool_call_events.assert_contains_any([BAND_EVENT_TOOL_NAME])


@with_adapters(Adapter.COPILOT_ACP, **TOOL_AGENT)
@flaky_model("the ACP agent may occasionally miss the explicit tool-only request")
@pytest.mark.timeout(extra=180)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_band_tool_result_is_a_single_clean_payload(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    """A Band tool's tool_result event carries the tool's output exactly once.

    An MCP bridge that forwards both a result's readable text and its
    structuredContent companion into one block duplicates the payload -- the
    room event then reads as the same JSON twice (once readable, once
    re-encoded). The room event's content is the serialized
    ``ToolResultRoomEvent`` wrapper (``name``/``output``/``tool_call_id``/
    ``is_error``); the contract this checks is on the decoded ``output``
    field: a single well-formed JSON document, the platform's actual response.

    The marker proves the tool ran (via the thought it posted); it is NOT
    asserted inside the tool_result, because the platform's create-event
    response (``{id, message_type, success}``) does not echo the content. The
    check is scoped to the Band tool's results by the wrapper's ``name`` (see
    ``BAND_EVENT_TOOL_NAME``): Copilot also narrates its own internal tools
    (e.g. skill loading), whose outputs are legitimately plain text.
    """
    marker = unique_marker("acp-result")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-tool-result-clean", participants=[agent.id]
    )

    async with reply_capture(room_id) as capture:
        mid = await user_ops.send_message(
            room_id,
            emit_event_instruction(MessageType.THOUGHT, marker),
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)
        thoughts = await capture.thoughts(sender_id=agent.id)
        tool_results = await capture.tool_results(sender_id=agent.id)

    thoughts.assert_contains_any([marker])
    band_results = tool_results.named(BAND_EVENT_TOOL_NAME)
    band_results.assert_present(what=f"a {BAND_EVENT_TOOL_NAME} tool_result")
    band_results.assert_json_output()


def skip_without_github_token(settings: BaselineSettings) -> None:
    """Hosted-auth coverage is optional; the BYOK cells are the lane's bar."""
    if not settings.backends.github_token:
        pytest.skip("GITHUB_TOKEN unset — the Copilot-hosted smokes need one")


def hermetic_copilot_config(
    settings: BaselineSettings,
    work_dir: Path,
    *,
    hosted: bool = False,
    model: str | None = None,
    reasoning_effort: str | None = None,
    inject_band_tools: bool = True,
) -> Any:
    """A per-test ``CopilotACPAdapterConfig`` with a fresh cwd + ``COPILOT_HOME``.

    The fresh home makes each config hermetic (host extensions and session
    state cannot steer the turn) and makes a later config's ACP
    ``session/load`` deterministically miss. ``hosted=False`` (default)
    mirrors the registry builder's Anthropic BYOK env (``copilot_acp_env``);
    ``hosted=True`` omits it and authenticates with ``github_token`` — the
    Copilot-hosted path production users run. ``model`` / ``reasoning_effort``
    set the typed selection the adapter applies from each session's
    advertised catalog; a hosted ``model`` defaults to
    ``settings.backends.copilot_hosted_model``, since the env's
    ``COPILOT_MODEL`` does not pick an ACP session's model.
    ``inject_band_tools=False`` leaves the agent without Band tools, so the
    adapter posts its text as the room reply.
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapterConfig,
    )

    home = copilot_home_dir(str(work_dir))
    if hosted and model is None:
        model = settings.backends.copilot_hosted_model
    kwargs: dict[str, Any] = {
        "cwd": str(work_dir),
        "custom_section": "Keep responses short and concise.",
        "env": ({"COPILOT_HOME": home} if hosted else copilot_acp_env(settings, home)),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "inject_band_tools": inject_band_tools,
    }
    if hosted:
        kwargs["github_token"] = settings.backends.github_token
    if settings.backends.copilot_command.strip():
        kwargs["command"] = tuple(settings.backends.copilot_command.split())
    return CopilotACPAdapterConfig(**kwargs)


async def reply_with(
    capture: ReplyCapture,
    *,
    marker: str,
    user_ops: UserOps,
    identity: ProvisionedAgent,
    settings: BaselineSettings,
) -> None:
    """One room turn whose reply must carry ``marker``."""
    mark = capture.messages.snapshot()
    mid = await user_ops.send_message(
        capture.room_id,
        f"Reply with one short sentence that includes the marker {marker}.",
        mention_id=identity.id,
        mention_name=identity.name,
    )
    replies = await capture.wait_for_reply(
        mid, identity.id, since=mark, deadline_s=settings.e2e_timeout
    )
    replies.assert_contains_any([marker])


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.COPILOT_CLI)
@pytest.mark.timeout(extra=180)  # Copilot CLI cold boot + hosted-auth handshake
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "reasoning_effort",
    # A non-default effort copilot_hosted_model advertises (its default: "medium").
    [pytest.param(None, id="default"), pytest.param("high", id="typed-effort")],
)
async def test_copilot_hosted_auth_replies(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
    reasoning_effort: str | None,
) -> None:
    """One reply turn on Copilot-hosted auth (GITHUB_TOKEN, no BYOK).

    The matrix cells run Anthropic BYOK to spare the monthly Copilot-hosted
    quota, but the hosted path is the one production users run — one cheap
    turn per cell keeps it proven. Only hosted sessions advertise model/effort
    selects, so the typed-effort cell proves a typed selection is accepted
    against Copilot's live catalog. Band tools stay off: with them, a hosted
    turn keeps calling band_send_message after its reply and never ends.
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapter,
    )

    skip_without_github_token(baseline_settings)

    identity = await resource_manager.provision_agent("copilot-hosted-auth")
    room_id = await resource_manager.provision_room(
        title="e2e-copilot-hosted-auth", participants=[identity.id]
    )

    adapter = CopilotACPAdapter(
        hermetic_copilot_config(
            baseline_settings,
            tmp_path / "hosted",
            hosted=True,
            reasoning_effort=reasoning_effort,
            inject_band_tools=False,
        )
    )
    async with (
        running_agent(identity, adapter, baseline_settings),
        reply_capture(room_id) as capture,
    ):
        await reply_with(
            capture,
            marker=unique_marker("hosted"),
            user_ops=user_ops,
            identity=identity,
            settings=baseline_settings,
        )


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.COPILOT_CLI)
@pytest.mark.timeout(extra=180)  # Copilot CLI cold boot
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("hosted", "selection", "failure"),
    [
        # Under BYOK the provider env picks the model; no model select exists.
        pytest.param(
            False,
            {"model": "not-a-model"},
            "ACP session advertises no model option; available:",
            marks=requires(Dep.ANTHROPIC),
            id="byok-model",
        ),
        # Hosted sessions advertise per-model efforts; the refusal proves the
        # selection was checked against Copilot's live catalog.
        pytest.param(
            True,
            {"reasoning_effort": "not-an-effort"},
            'reasoning effort "not-an-effort" is not advertised for model',
            id="hosted-effort",
        ),
    ],
)
async def test_copilot_turn_fails_loudly_on_an_unadvertised_selection(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
    hosted: bool,
    selection: dict[str, str],
    failure: str,
) -> None:
    """A typed selection the session does not advertise fails the turn with a
    visible error naming what it offers, rather than being ignored."""
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapter,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- same acp extra as above
        CONFIG_FAILURE_PREFIX,
    )

    if hosted:
        skip_without_github_token(baseline_settings)

    identity = await resource_manager.provision_agent("copilot-unadvertised")
    room_id = await resource_manager.provision_room(
        title="e2e-copilot-unadvertised", participants=[identity.id]
    )

    adapter = CopilotACPAdapter(
        hermetic_copilot_config(
            baseline_settings, tmp_path / "copilot", hosted=hosted, **selection
        )
    )
    async with (
        running_agent(identity, adapter, baseline_settings),
        reply_capture(room_id) as capture,
    ):
        mid = await user_ops.send_message(
            room_id,
            "Reply with one short sentence.",
            mention_id=identity.id,
            mention_name=identity.name,
        )
        await capture.wait_for_processed(mid, identity.id)
        errors = await capture.errors(sender_id=identity.id)

    errors.assert_contains_any([CONFIG_FAILURE_PREFIX + failure])


def room_session_id(tasks: Tasks, room_id: str) -> str:
    """The ACP session the adapter last reported for ``room_id``, read the way
    it reads one back to restore it."""
    reported = (
        ACPClientHistoryConverter()
        .convert([{"metadata": metadata_to_dict(task.metadata)} for task in tasks])
        .room_to_session
    )
    assert room_id in reported, f"no ACP session reported for room {room_id}"
    return reported[room_id]


async def copilot_persisted_selection(
    config: Any, *, room_id: str, session_id: str
) -> ModelSelection:
    """What a fresh ``copilot --acp`` reports ``session_id`` runs after
    ``session/load``: Copilot's own record, not the adapter's. It is spawned
    by a fresh adapter's own runtime wiring (launcher, env, workspace)."""
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- the acp extra, as above
        CopilotACPAdapter,
    )
    from band.integrations.acp.model_selection import (  # noqa: PLC0415 -- the acp extra, as above
        locate_model_options,
    )
    from band.integrations.acp.session_config import (  # noqa: PLC0415 -- the acp extra, as above
        session_config_options,
    )

    spawner = CopilotACPAdapter(config)
    workspace = spawner._workspace(room_id)
    runtime = spawner._build_runtime(workspace)
    await runtime.start()
    try:
        loaded = await runtime.load_session_response(
            cwd=workspace, session_id=session_id, mcp_servers=[]
        )
    finally:
        await runtime.stop()
    assert loaded is not None, f"Copilot could not load session {session_id}"
    return locate_model_options(
        session_config_options(loaded) or ()
    ).current_selection()


# Probed as advertised; an account without them fails the switch naming
# what it offers.
SWITCHED = ModelSelection(model="gpt-5.4", reasoning_effort="low")


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.COPILOT_CLI)
# Two hosted turns plus a second Copilot CLI boot for the session/load check.
@pytest.mark.timeout(extra=300)
@pytest.mark.asyncio(loop_scope="session")
async def test_copilot_switches_model_and_effort_in_a_live_room(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """A runtime switch changes what Copilot itself runs for the room, and the
    room keeps replying on it.

    The check is Copilot's persisted session, read by a fresh process, so a
    switch that only updated the adapter's own record, or an effort Copilot
    silently ignored, fails. Band tools stay off: with them, a hosted turn
    keeps calling band_send_message after its reply and never ends.
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapter,
    )

    skip_without_github_token(baseline_settings)

    identity = await resource_manager.provision_agent("copilot-live-switch")
    room_id = await resource_manager.provision_room(
        title="e2e-copilot-live-switch", participants=[identity.id]
    )
    config = hermetic_copilot_config(
        baseline_settings,
        tmp_path / "copilot",
        hosted=True,
        reasoning_effort="high",
        inject_band_tools=False,
    )
    # Only a switch away from what the session starts on proves anything.
    assert config.model != SWITCHED.model, "COPILOT_HOSTED_MODEL is the switch target"
    adapter = CopilotACPAdapter(config)
    turn = partial(
        reply_with, user_ops=user_ops, identity=identity, settings=baseline_settings
    )

    async with (
        running_agent(identity, adapter, baseline_settings),
        reply_capture(room_id) as capture,
    ):
        await turn(capture, marker=unique_marker("before-switch"))
        await adapter.apply_model_selection(SWITCHED, room_id=room_id)
        # Also the prompt Copilot persists the switched session on.
        await turn(capture, marker=unique_marker("after-switch"))
        session_id = room_session_id(
            await capture.tasks(sender_id=identity.id), room_id
        )

    assert (
        await copilot_persisted_selection(
            config, room_id=room_id, session_id=session_id
        )
        == SWITCHED
    )


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.COPILOT_CLI, Dep.ANTHROPIC)
# Deliberately no flaky marker: two clean runs so far, and a rerun here would
# slow-surface a product bug that presents as a silent no-reply turn (the
# load-error path failed exactly that way once). Add flaky_infra only with an
# observed transient to cite.
# Two agent lifecycles, each booting a fresh Copilot CLI with an empty
# COPILOT_HOME (the session-load-miss setup) — the heaviest boot path here.
@pytest.mark.timeout(extra=300)
@pytest.mark.asyncio(loop_scope="session")
async def test_acp_recall_via_room_replay_when_session_load_misses(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """Recall must survive a restart that invalidates ACP's native session resume.

    A plain stop/restart against surviving Copilot state would let ACP
    ``session/load`` answer for free, so a green recall would not prove the
    fallback. This test gives each phase a fresh ``COPILOT_HOME``: phase 2's
    ``session/load`` finds no state and recall can only flow through the Band
    room transcript the adapter replays into the new session's first prompt.
    Two facts are asserted after the restart: a tracking marker the USER stated
    (plain replay recall), and a calibration answer the AGENT produced in
    phase 1 — the user never utters that answer, so the agent's own replayed
    reply lines are its only possible source (the regression case for a replay
    that drops the agent's side of the transcript).
    """
    from band.adapters.copilot_acp import (  # noqa: PLC0415 -- copilot_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        CopilotACPAdapter,
    )

    tracking_marker = unique_marker("acp-replay")
    agent_fact = "blue"

    def make_adapter(phase: str) -> CopilotACPAdapter:
        return CopilotACPAdapter(
            hermetic_copilot_config(baseline_settings, tmp_path / phase)
        )

    identity = await resource_manager.provision_agent("acp-session-load-miss")
    room_id = await resource_manager.provision_room(
        title="e2e-acp-session-load-miss", participants=[identity.id]
    )

    # Phase 1: seed a user fact and make the agent produce its own fact.
    async with (
        running_agent(identity, make_adapter("phase1"), baseline_settings),
        reply_capture(room_id) as capture,
    ):
        mid = await user_ops.send_message(
            room_id,
            "Create a short project log note for later reference. The "
            f"tracking marker is {tracking_marker}. Also answer this "
            "calibration question inside your reply: what color is a "
            "clear daytime sky? Reply in one short sentence that includes "
            "the tracking marker and the color answer.",
            mention_id=identity.id,
            mention_name=identity.name,
        )
        replies = await capture.wait_for_reply(
            mid, identity.id, deadline_s=baseline_settings.e2e_timeout
        )
        replies.assert_contains_any([tracking_marker])
        # Must be in the transcript now, or phase 2 has nothing to replay.
        replies.assert_contains_any([agent_fact])

    # Phase 2: fresh process AND fresh COPILOT_HOME — session/load misses, so
    # recall can only come from the replayed Band room transcript.
    async with (
        running_agent(identity, make_adapter("phase2"), baseline_settings),
        reply_capture(room_id) as capture,
    ):
        mid = await user_ops.send_message(
            room_id,
            "From the earlier project log, what was the tracking marker "
            "and what color answer did you give? Reply with both.",
            mention_id=identity.id,
            mention_name=identity.name,
        )
        replies = await capture.wait_for_reply(
            mid, identity.id, deadline_s=baseline_settings.e2e_timeout
        )
        replies.assert_contains_any([tracking_marker])
        # The user never uttered this answer — only the agent's own
        # replayed phase-1 reply can supply it.
        replies.assert_contains_any([agent_fact])
