"""Copilot replies on Copilot-hosted auth (GITHUB_TOKEN, no BYOK)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.adapters.copilotacp.kit import (
    hermetic_copilot_config,
    reply_with,
    skip_without_github_token,
)
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestHostedAuth:
    # Bespoke build exposes no framework; pin scheduling to backends.
    @lane(Lane.BACKENDS)
    @requires(Dep.COPILOT_CLI)
    @pytest.mark.timeout(extra=180)  # Copilot CLI cold boot + hosted-auth handshake
    @pytest.mark.asyncio(loop_scope="session")
    @pytest.mark.parametrize(
        "reasoning_effort",
        # A non-default effort copilot_hosted_model advertises (its default: "medium").
        [pytest.param(None, id="default"), pytest.param("high", id="typed-effort")],
    )
    async def test_copilot_hosted_auth_replies(
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
        reasoning_effort: str | None,
    ) -> None:
        """One reply turn on Copilot-hosted auth (GITHUB_TOKEN, no BYOK).

        The matrix cells run Anthropic BYOK to spare the monthly Copilot-hosted
        quota, but the hosted path is the one production users run — one cheap
        turn per cell keeps it proven. Only hosted sessions advertise model/effort
        selects, so the typed-effort cell proves a typed selection is accepted
        against Copilot's live catalog. Replies use the injected Band tools.
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
