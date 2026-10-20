from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def available_crewai(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    fake_module = MagicMock()
    fake_flow_module = MagicMock()
    fake_flow_module.Flow = type("Flow", (), {})
    fake_flow_module.start = MagicMock()
    fake_flow_module.listen = MagicMock()
    fake_flow_module.router = MagicMock()
    fake_flow_module.and_ = MagicMock()
    fake_flow_module.or_ = MagicMock()
    fake_module.flow = MagicMock()
    fake_module.flow.flow = fake_flow_module

    monkeypatch.setitem(sys.modules, "crewai", fake_module)
    monkeypatch.setitem(sys.modules, "crewai.flow", fake_module.flow)
    monkeypatch.setitem(sys.modules, "crewai.flow.flow", fake_flow_module)
    yield
