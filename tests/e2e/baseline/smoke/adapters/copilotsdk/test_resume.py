"""Isolated Copilot SDK live behavior."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.adapters.copilot_sdk import (
    _COPILOT_SDK_AVAILABLE,
    CopilotSDKAdapter,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.flaky import flaky_infra
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    running_agent,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps

pytestmark = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)


class TestResumeMiss:
    @lane(Lane.CORE)  # bespoke build exposes no framework; pin scheduling to core
    @requires(Dep.ANTHROPIC)
    @flaky_infra(
        "two fresh Copilot runtime boots (resume-miss setup) can time out transiently"
    )
    # Two agent lifecycles, each booting a fresh Copilot runtime into an empty
    # base_directory (the resume-miss setup) — the heaviest boot path in this file.
    @pytest.mark.timeout(extra=180)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_copilot_recall_via_injected_history_when_resume_misses(
        self,
        copilot_adapter: Callable[..., CopilotSDKAdapter],
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Any,
    ) -> None:
        """Recall must survive a restart that invalidates Copilot's *native* resume.

        A rejoin against the same on-disk state (a plain stop/restart) would let
        Copilot's own session resume answer for free. This test gives each phase a
        fresh ``base_directory``, so phase 2's resume finds no state and recall
        must flow through the converter's injected text history instead. Two facts
        are asserted after the restart: a tracking marker the USER stated (plain
        injected-history recall), and a calibration answer the AGENT produced in
        phase 1 — the user never utters that answer, so the agent's own injected
        replies are its only possible source (the regression case for one-sided
        injected history).
        """

        tracking_marker = unique_marker("marker")
        agent_fact = "blue"

        def make_adapter(phase: str) -> CopilotSDKAdapter:
            # A fresh base_directory per phase: phase 2 has no on-disk session
            # state, so Copilot's native resume cannot help.
            return copilot_adapter(base_directory=str(tmp_path / phase))

        identity = await resource_manager.provision_agent("copilot-resume-miss")
        room_id = await resource_manager.provision_room(
            title="e2e-copilot-resume-miss", participants=[identity.id]
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
            # The user never uttered this answer; it must come from the
            # agent's phase-1 reply when phase 2 reconstructs history.
            replies.assert_contains_any([agent_fact])

        # Phase 2: fresh process state AND fresh Copilot state directory — recall
        # can only come from the platform history the adapter injects.
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
            # injected phase-1 reply can supply it.
            replies.assert_contains_any([agent_fact])
