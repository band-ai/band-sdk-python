"""Every approval dialect builds its agent in the setup's approval mode and features."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    AgentSetup,
    cursor_test_adapter,
)
from tests.e2e.baseline.toolkit.adapters import Adapter

SILENT_MEMORY = AdapterFeatures(emit=frozenset(), capabilities={Capability.MEMORY})

# Claude nests its approval policy; the other dialects configure it flat.
APPROVAL_OF: dict[Adapter, Callable[[Any], str]] = {
    Adapter.CLAUDE_SDK: lambda config: config.approvals.mode,
    Adapter.CODEX: lambda config: config.approval_mode,
    Adapter.CURSOR_ACP: lambda config: config.approval_mode,
    Adapter.OPENCODE: lambda config: config.approval_mode,
}


class TestDialectSetup:
    @pytest.mark.parametrize("adapter", DIALECTS)
    def test_setup_approval_and_features_reach_the_built_agent(
        self, adapter: Adapter, tmp_path: Path
    ) -> None:
        setup = AgentSetup(
            tmp_path, 1.0, features=SILENT_MEMORY, approval="auto_accept"
        )
        built: SimpleAdapter[Any] = DIALECTS[adapter].build(BaselineSettings(), setup)
        assert (APPROVAL_OF[adapter](built.config), built.features) == (
            "auto_accept",
            SILENT_MEMORY,
        )

    @pytest.mark.parametrize("adapter", DIALECTS)
    def test_default_setup_keeps_manual_asks_and_adapter_defaults(
        self, adapter: Adapter, tmp_path: Path
    ) -> None:
        built: SimpleAdapter[Any] = DIALECTS[adapter].build(
            BaselineSettings(), AgentSetup(tmp_path, 1.0)
        )
        assert (APPROVAL_OF[adapter](built.config), built.features) == (
            "manual",
            AdapterFeatures(emit=type(built).SUPPORTED_EMIT),
        )

    def test_cursor_workflow_capabilities_replace_only_that_setup_feature(
        self, tmp_path: Path
    ) -> None:
        setup = AgentSetup(tmp_path, 1.0, features=SILENT_MEMORY)
        built = cursor_test_adapter(
            BaselineSettings(), setup, capabilities={Capability.CONTACTS}
        )
        assert built.features == AdapterFeatures(
            emit=frozenset(), capabilities={Capability.CONTACTS}
        )
