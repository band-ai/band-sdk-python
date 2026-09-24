"""Live smoke coverage for the Kiro CLI ACP integration's L4 rehydration story.

Modeled on ``test_copilot_acp.py``, adjusted for Kiro's auth model: unlike
Copilot, ``kiro-cli`` has no BYOK/provider-swap (see ``Dep.KIRO_CLI``), so
there is no separate hosted-auth smoke here — every cell in the shared matrix
already runs on real Kiro auth (``KIRO_API_KEY``) or skips-with-reason without
one. The two tests here are specific to Kiro's genuine native ``session/load``
support (``loadSession: true``), which Copilot's own hermetic BYOK setup never
exercises (it always forces a miss).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from band import create_room_workspace_resolver
from band.core.types import MessageType
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.builders import kiro_acp_env, kiro_home_dir
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

_SESSION_EVENT_MARKER = "acp_client_session_id"  # RoomTurnEmitter's bookkeeping key


def hermetic_kiro_config(settings: BaselineSettings, work_dir: Path) -> Any:
    """A per-test ``KiroACPAdapterConfig`` with a fresh workspace + ``KIRO_HOME``.

    Mirrors ``hermetic_copilot_config``, minus the hosted/BYOK branch Kiro has
    no equivalent of: every cell here already runs on real Kiro auth
    (``KIRO_API_KEY``, see ``Dep.KIRO_CLI``), so there's no separate
    hosted-auth path to switch between.
    """
    from band.adapters.kiro_acp import (  # noqa: PLC0415 -- kiro_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        KiroACPAdapterConfig,
    )

    home = kiro_home_dir(str(work_dir))
    kwargs: dict[str, Any] = {
        "workspace_for_room": create_room_workspace_resolver(str(work_dir)),
        "custom_section": "Keep responses short and concise.",
        "env": kiro_acp_env(settings, home),
    }
    if settings.backends.kiro_command.strip():
        kwargs["command"] = tuple(settings.backends.kiro_command.split())
    return KiroACPAdapterConfig(**kwargs)


async def acp_session_id(capture: ReplyCapture, sender_id: str) -> str:
    """The ACP session id from this turn's trailing bookkeeping ``task`` event.

    ``RoomTurnEmitter`` posts one such event per turn, carrying the session id
    the turn actually ran on (see ``_SESSION_EVENT_MARKER``). A restart that
    resumes the *same* session (a native ``session/load`` hit) reuses this id;
    one that falls back to Band's room-replay mints a fresh session and a
    fresh id -- the signal ``test_kiro_acp_recall_via_native_session_load``
    distinguishes on.
    """
    events = await capture.events(MessageType.TASK, sender_id=sender_id)
    session_ids = {
        event.metadata[_SESSION_EVENT_MARKER]
        for event in events
        if isinstance(event.metadata, dict) and _SESSION_EVENT_MARKER in event.metadata
    }
    assert len(session_ids) == 1, (
        f"expected exactly one ACP session id in this turn's bookkeeping "
        f"events, got {session_ids!r}"
    )
    return next(iter(session_ids))


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.KIRO_CLI)
# Deliberately no flaky marker: mirrors Copilot's equivalent
# (test_acp_recall_via_room_replay_when_session_load_misses) -- see its comment.
# Two agent lifecycles, each booting a fresh Kiro CLI with an empty KIRO_HOME
# (the session-load-miss setup) -- the heaviest boot path here.
@pytest.mark.timeout(extra=300)
@pytest.mark.asyncio(loop_scope="session")
async def test_kiro_acp_recall_via_room_replay_when_session_load_misses(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """Recall must survive a restart that invalidates ACP's native session resume.

    Direct port of Copilot's equivalent test. A plain stop/restart against
    surviving Kiro state would let ACP ``session/load`` answer for free, so a
    green recall would not prove the fallback. This test gives each phase a
    fresh ``KIRO_HOME``: phase 2's ``session/load`` finds no state and recall
    can only flow through the Band room transcript the adapter replays into
    the new session's first prompt. Two facts are asserted after the restart:
    a tracking marker the USER stated (plain replay recall), and a calibration
    answer the AGENT produced in phase 1 -- the user never utters that answer,
    so the agent's own replayed reply lines are its only possible source.
    """
    from band.adapters.kiro_acp import (  # noqa: PLC0415 -- kiro_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        KiroACPAdapter,
    )

    tracking_marker = unique_marker("kiro-replay")
    agent_fact = "blue"

    def make_adapter(phase: str) -> KiroACPAdapter:
        return KiroACPAdapter(hermetic_kiro_config(baseline_settings, tmp_path / phase))

    identity = await resource_manager.provision_agent("kiro-session-load-miss")
    room_id = await resource_manager.provision_room(
        title="e2e-kiro-acp-session-load-miss", participants=[identity.id]
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

    # Phase 2: fresh process AND fresh KIRO_HOME -- session/load misses, so
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
        # The user never uttered this answer -- only the agent's own
        # replayed phase-1 reply can supply it.
        replies.assert_contains_any([agent_fact])


@lane(Lane.BACKENDS)  # bespoke build exposes no framework; pin scheduling to backends
@requires(Dep.KIRO_CLI)
@pytest.mark.timeout(extra=300)
@pytest.mark.asyncio(loop_scope="session")
async def test_kiro_acp_recall_via_native_session_load(
    baseline_settings: BaselineSettings,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
    tmp_path: Any,
) -> None:
    """Kiro genuinely advertises ``loadSession: true`` -- prove the native
    ``session/load`` resume path actually succeeds, not just the room-replay
    fallback the miss-path test above exercises. Copilot's own hermetic BYOK
    setup always forces a miss, so it has no equivalent of this test.

    Same ``KIRO_HOME`` and workspace across the restart, so kiro-cli's own
    on-disk session state survives and ``_restore_session``'s ``session/load``
    call should hit rather than miss. Recall alone doesn't distinguish a hit
    from the fallback (both produce correct recall) -- the distinguishing
    proof is ``acp_session_id``: a hit resumes the *same* ACP session id
    across the restart, while a fallback mints a fresh one via
    ``_create_session``.
    """
    from band.adapters.kiro_acp import (  # noqa: PLC0415 -- kiro_acp imports the acp (agent-client-protocol) extra at its own top level; not installed in every lane's venv
        KiroACPAdapter,
    )

    tracking_marker = unique_marker("kiro-resume")
    agent_fact = "blue"
    work_dir = tmp_path / "kiro-resume"

    def make_adapter() -> KiroACPAdapter:
        return KiroACPAdapter(hermetic_kiro_config(baseline_settings, work_dir))

    identity = await resource_manager.provision_agent("kiro-session-load-hit")
    room_id = await resource_manager.provision_room(
        title="e2e-kiro-acp-session-load-hit", participants=[identity.id]
    )

    async with (
        running_agent(identity, make_adapter(), baseline_settings),
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
        replies.assert_contains_any([agent_fact])
        first_session_id = await acp_session_id(capture, identity.id)

    # Same KIRO_HOME and the same workspace -- a fresh process, but kiro-cli's
    # own on-disk session state survives, so session/load should hit.
    async with (
        running_agent(identity, make_adapter(), baseline_settings),
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
        replies.assert_contains_any([agent_fact])
        second_session_id = await acp_session_id(capture, identity.id)

    assert second_session_id == first_session_id, (
        "expected kiro-cli's native session/load to resume the same ACP "
        f"session across the restart, got {first_session_id!r} then "
        f"{second_session_id!r} (a fresh id means the room-replay fallback "
        "ran instead -- session/load missed)"
    )
