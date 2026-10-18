"""Exercise the real Windows search rules without launching another process."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from band.adapters import copilot_acp
from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from tests.adapters.copilotprobe import CopilotProbeSDK


@pytest.fixture
def copilot_probe_sdk(monkeypatch: pytest.MonkeyPatch) -> CopilotProbeSDK:
    module = pytest.importorskip("copilot")
    sdk = CopilotProbeSDK(module)
    monkeypatch.setattr(module, "CopilotClient", sdk.factory)
    return sdk


def emulate_windows_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run CPython's actual Windows which branch with the host filesystem."""
    windows_os = SimpleNamespace(**vars(os))
    windows_os.name = "nt"
    windows_os.pathsep = ";"
    monkeypatch.setattr(copilot_acp, "os", windows_os)
    monkeypatch.setattr(shutil, "os", windows_os)
    monkeypatch.setattr(shutil, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        shutil,
        "_winapi",
        SimpleNamespace(NeedCurrentDirectoryForExePath=lambda _: True),
        raising=False,
    )


async def test_windows_configured_path_does_not_search_the_host_current_directory(
    copilot_probe_sdk: CopilotProbeSDK,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_cwd = tmp_path / "host-cwd"
    configured_cwd = tmp_path / "configured-cwd"
    configured_bin = configured_cwd / "bin"
    host_cwd.mkdir()
    configured_bin.mkdir(parents=True)
    selected = configured_bin / "copilot.exe"
    for executable in (host_cwd / "copilot.exe", selected):
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.chdir(host_cwd)
    monkeypatch.setenv("PATHEXT", ".exe")
    emulate_windows_search(monkeypatch)
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=("copilot", "--acp"),
            cwd=str(configured_cwd),
            env={"PATH": str(configured_bin), "PATHEXT": ".exe"},
        )
    )

    await adapter.probe_models(timeout_s=1)

    assert copilot_probe_sdk.launches[0]["connection"].path == str(selected.resolve())
    assert not copilot_probe_sdk.open_clients
    assert not adapter._rooms


async def test_windows_extensions_come_from_the_configured_environment(
    copilot_probe_sdk: CopilotProbeSDK,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_cwd = tmp_path / "host-cwd"
    configured_cwd = tmp_path / "configured-cwd"
    configured_bin = configured_cwd / "bin"
    host_cwd.mkdir()
    configured_bin.mkdir(parents=True)
    selected = configured_bin / "copilot.cmd"
    for executable in (configured_bin / "copilot.exe", selected):
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.chdir(host_cwd)
    monkeypatch.setenv("PATHEXT", ".exe")
    emulate_windows_search(monkeypatch)
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=("copilot", "--acp"),
            cwd=str(configured_cwd),
            env={"PATH": str(configured_bin), "PATHEXT": ".cmd"},
        )
    )

    await adapter.probe_models(timeout_s=1)

    assert copilot_probe_sdk.launches[0]["connection"].path == str(selected.resolve())
    assert not copilot_probe_sdk.open_clients
    assert not adapter._rooms
