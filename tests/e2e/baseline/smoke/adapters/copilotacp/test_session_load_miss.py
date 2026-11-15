"""Room recall survives a restart whose ACP ``session/load`` misses."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.copilotacp.kit import hermetic_copilot_config
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestSessionLoadMiss:
    # Bespoke build exposes no framework; pin scheduling to backends.
    @lane(Lane.BACKENDS)
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
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
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

        tracking_marker = unique_marker("acpreplay")
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
