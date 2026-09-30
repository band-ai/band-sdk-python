"""Typed ``turn_timeout_s`` reaches the adapter on the config-based OMP and Copilot ACP hosts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.omp_acp import OmpACPAdapter, OmpACPAdapterConfig
from band.integrations.acp.client_adapter import ACPClientAdapter


@pytest.mark.parametrize(
    ("adapter_cls", "config_cls"),
    [
        (OmpACPAdapter, OmpACPAdapterConfig),
        (CopilotACPAdapter, CopilotACPAdapterConfig),
    ],
    ids=["omp", "copilot"],
)
def test_typed_config_sets_the_turn_timeout(
    adapter_cls: Callable[[Any], ACPClientAdapter], config_cls: Callable[..., Any]
) -> None:
    assert adapter_cls(config_cls(turn_timeout_s=1800.0))._turn_timeout_s == 1800.0
