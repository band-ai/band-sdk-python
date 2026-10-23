"""Load the standalone mixin without adding it to the SDK package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tests.paths import CLAUDE_MIXIN_DIR


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    name = "bandclaudemixin"
    spec = importlib.util.spec_from_file_location(name, CLAUDE_MIXIN_DIR / "bridge.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bridge: ModuleType
) -> SimpleNamespace:
    monkeypatch.setenv("BAND_API_KEY", "unit-test-key")
    monkeypatch.setenv("BAND_REST_URL", "https://app.band.ai")
    monkeypatch.setenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    cli = str(tmp_path / "claude")
    monkeypatch.setattr(bridge.shutil, "which", lambda _: cli)
    return SimpleNamespace(
        agent_id="test-agent",
        workspace=tmp_path,
        model="sonnet",
        check_claude=False,
        check_band=False,
    )
