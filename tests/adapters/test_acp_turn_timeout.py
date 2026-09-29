"""Typed ``turn_timeout_s`` on the config-based OMP and Copilot ACP adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.adapters.omp_acp import OmpACPAdapter, OmpACPAdapterConfig
from band.integrations.acp.client_adapter import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    ACPClientAdapter,
)

AdapterFactory = Callable[..., ACPClientAdapter]


def _omp(config_timeout: float | None = None, **kwargs: Any) -> ACPClientAdapter:
    config = (
        OmpACPAdapterConfig()
        if config_timeout is None
        else OmpACPAdapterConfig(turn_timeout_s=config_timeout)
    )
    return OmpACPAdapter(config, **kwargs)


def _copilot(config_timeout: float | None = None, **kwargs: Any) -> ACPClientAdapter:
    config = (
        CopilotACPAdapterConfig()
        if config_timeout is None
        else CopilotACPAdapterConfig(turn_timeout_s=config_timeout)
    )
    return CopilotACPAdapter(config, **kwargs)


pytestmark = pytest.mark.parametrize("build", [_omp, _copilot], ids=["omp", "copilot"])


def test_default_is_unchanged(build: AdapterFactory) -> None:
    assert build()._turn_timeout_s == DEFAULT_TURN_TIMEOUT_SECONDS


def test_typed_config_sets_the_turn_timeout(build: AdapterFactory) -> None:
    assert build(1800.0)._turn_timeout_s == 1800.0


def test_untyped_kwarg_keeps_working(build: AdapterFactory) -> None:
    assert build(turn_timeout_s=900.0)._turn_timeout_s == 900.0


def test_deliberate_config_value_wins_over_the_kwarg(build: AdapterFactory) -> None:
    assert build(1800.0, turn_timeout_s=900.0)._turn_timeout_s == 1800.0
