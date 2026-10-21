"""Parametrized fixtures for framework conformance tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.toolkit import provisioning
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_configs.converters import CONVERTER_CONFIGS


@pytest.fixture(params=CONVERTER_CONFIGS, ids=lambda c: c.framework_id)
def converter_config(request):
    """Yield each ConverterConfig in turn."""
    return request.param


@pytest.fixture(params=ADAPTER_CONFIGS, ids=lambda c: c.framework_id)
def adapter_config(request):
    """Yield each AdapterConfig in turn."""
    return request.param


@pytest.fixture
def make_converter(converter_config):
    """Factory fixture: create a converter from the current config."""

    def _make(**kwargs):
        return converter_config.converter_factory(**kwargs)

    return _make


@pytest.fixture
def output(converter_config):
    """Output adapter for the current converter config."""
    return converter_config.output_adapter


@pytest.fixture
def started_with(monkeypatch: pytest.MonkeyPatch) -> list[SessionConfig | None]:
    """The session config of each agent started."""
    configs: list[SessionConfig | None] = []

    @asynccontextmanager
    async def running_agent(
        provisioned: ProvisionedAgent,
        adapter: Any,
        settings: Any,
        session_config: SessionConfig | None = None,
    ) -> AsyncIterator[ProvisionedAgent]:
        configs.append(session_config)
        yield provisioned

    monkeypatch.setattr(provisioning, "running_agent", running_agent)
    return configs
