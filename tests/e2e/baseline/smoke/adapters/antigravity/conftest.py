"""Keep Antigravity native state across fresh adapter instances."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from band.workspaces import create_room_workspace_resolver
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import REPLY_PROMPT
from tests.e2e.baseline.toolkit.antigravity import (
    antigravity_api_key,
    antigravity_command,
)

if TYPE_CHECKING:
    from band.adapters.antigravity_acp import AntigravityACPAdapter


@pytest.fixture
def persistent_antigravity(
    baseline_settings: BaselineSettings, tmp_path: Path
) -> Callable[[], AntigravityACPAdapter]:
    from band.adapters.antigravity_acp import (  # noqa: PLC0415 -- optional acp extra
        AntigravityACPAdapter,
        AntigravityACPAdapterConfig,
    )

    config = AntigravityACPAdapterConfig(
        command=antigravity_command(baseline_settings),
        gemini_api_key=antigravity_api_key(baseline_settings),
        gemini_home=str(tmp_path / "native"),
        model=baseline_settings.llm_models.gemini_model,
        custom_section=REPLY_PROMPT,
        approval_mode="auto_decline",
    )
    workspace = create_room_workspace_resolver(tmp_path / "workspaces")

    def build() -> AntigravityACPAdapter:
        return AntigravityACPAdapter(config, workspace_for_room=workspace)

    return build
