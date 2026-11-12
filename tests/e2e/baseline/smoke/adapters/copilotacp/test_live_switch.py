"""A runtime model switch changes what Copilot itself runs for a live room."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from band.core.model_catalog import ModelSelection
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.copilotacp.kit import (
    copilot_persisted_selection,
    hermetic_copilot_config,
    reply_with,
    room_session_id,
    skip_without_github_token,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Probed as advertised; an account without them fails the switch naming
# what it offers.
SWITCHED = ModelSelection(model="gpt-5.4", reasoning_effort="low")


class TestLiveSwitch:
    # Bespoke build exposes no framework; pin scheduling to backends.
    @lane(Lane.BACKENDS)
    @requires(Dep.COPILOT_CLI)
    # Two hosted turns plus a second Copilot CLI boot for the session/load check.
    @pytest.mark.timeout(extra=300)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_copilot_switches_model_and_effort_in_a_live_room(
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
    ) -> None:
        """A runtime switch changes what Copilot itself runs for the room, and the
        room keeps replying on it.

        The check is Copilot's persisted session, read by a fresh process, so a
        switch that only updated the adapter's own record, or an effort Copilot
        silently ignored, fails.
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
        )
        # Only a switch away from what the session starts on proves anything.
        assert config.model != SWITCHED.model, (
            "COPILOT_HOSTED_MODEL is the switch target"
        )
        adapter = CopilotACPAdapter(config)
        turn = partial(
            reply_with, user_ops=user_ops, identity=identity, settings=baseline_settings
        )

        async with (
            running_agent(identity, adapter, baseline_settings),
            reply_capture(room_id) as capture,
        ):
            await turn(capture, marker=unique_marker("before"))
            await adapter.apply_model_selection(SWITCHED, room_id=room_id)
            # Also the prompt Copilot persists the switched session on.
            await turn(capture, marker=unique_marker("after"))
            session_id = room_session_id(
                await capture.tasks(sender_id=identity.id), room_id
            )

        assert (
            await copilot_persisted_selection(
                config, room_id=room_id, session_id=session_id
            )
            == SWITCHED
        )
