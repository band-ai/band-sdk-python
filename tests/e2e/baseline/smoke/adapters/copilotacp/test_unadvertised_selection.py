"""A typed selection Copilot does not advertise fails the turn loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.copilotacp.kit import (
    hermetic_copilot_config,
    skip_without_github_token,
)
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestUnadvertisedSelection:
    # Bespoke build exposes no framework; pin scheduling to backends.
    @lane(Lane.BACKENDS)
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
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
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
