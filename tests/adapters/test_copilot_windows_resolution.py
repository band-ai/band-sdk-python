"""Verify configured Windows search at real SDK and process boundaries."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from band.adapters import copilot_acp
from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.core.exceptions import BandConfigError
from tests.adapters import copilotwire
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


@pytest.fixture
def windows_sdk_children(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[subprocess.Popen[bytes]]]:
    """Retain real SDK startup while emulating Windows image lookup at Popen."""
    client_module = pytest.importorskip("copilot.client")
    children: list[subprocess.Popen[bytes]] = []

    def spawn(command: list[str], **options: object) -> subprocess.Popen[bytes]:
        image = Path(command[0])
        if not image.is_absolute():
            if image.parent != Path("."):
                image = (Path.cwd() / image).resolve()
            else:
                filename = image.name if image.suffix else image.name + ".exe"
                # Windows uses the parent's search context, not child env/cwd.
                directories = (
                    Path.cwd(),
                    *(Path(entry) for entry in os.get_exec_path()),
                )
                for directory in directories:
                    candidate = directory / filename
                    if candidate.is_file():
                        image = candidate.resolve()
                        break
                else:
                    raise FileNotFoundError(command[0])
        # Fixture images contain Python even when their Windows suffix is native.
        child = subprocess.Popen([sys.executable, str(image), *command[1:]], **options)
        children.append(child)
        return child

    windows_subprocess = SimpleNamespace(**vars(subprocess))
    windows_subprocess.Popen = spawn
    monkeypatch.setattr(client_module, "subprocess", windows_subprocess)
    try:
        yield children
    finally:
        for child in children:
            if child.poll() is None:
                subprocess.Popen.kill(child)
            subprocess.Popen.wait(child, timeout=5)


def write_rpc_peer(
    executable: Path, identity: str, launch_trace: Path, rpc_trace: Path
) -> None:
    """Write an executable whose identity is recorded by the actual child."""
    executable.write_text(
        f"#!{sys.executable}\n"
        "import runpy\n"
        "from pathlib import Path\n"
        f"Path({str(launch_trace)!r}).write_text({identity!r}, encoding='utf-8')\n"
        f"peer = runpy.run_path({str(Path(copilotwire.__file__).resolve())!r})\n"
        f"peer['serve'](Path({str(rpc_trace)!r}), linger_after_eof=False)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


def write_node_peer(executable: Path, identity: str, launch_trace: Path) -> None:
    """Execute the fixture script through a filesystem-backed interpreter peer."""
    executable.write_text(
        f"#!{sys.executable}\n"
        "import runpy\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(launch_trace)!r}).write_text({identity!r}, encoding='utf-8')\n"
        "script = sys.argv[1]\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(script, run_name='__main__')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)


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


@pytest.mark.parametrize("absolute_command", [False, True])
async def test_windows_qualified_command_uses_configured_extension_with_real_sdk(
    absolute_command: bool,
    windows_sdk_children: list[subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_cwd = tmp_path / "host-cwd"
    configured_cwd = tmp_path / "configured-cwd"
    configured_bin = configured_cwd / "tools"
    host_cwd.mkdir()
    configured_bin.mkdir(parents=True)
    launch_trace = tmp_path / "launched-peer.txt"
    rpc_trace = tmp_path / "wire-methods.txt"
    write_rpc_peer(
        configured_bin / "copilot.cmd", "configured-cmd", launch_trace, rpc_trace
    )
    write_rpc_peer(
        configured_bin / "copilot.exe", "ambient-exe", launch_trace, rpc_trace
    )
    monkeypatch.chdir(host_cwd)
    monkeypatch.setenv("PATHEXT", ".exe")
    emulate_windows_search(monkeypatch)
    command = str(configured_bin / "copilot") if absolute_command else "tools/copilot"
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=(command, "--acp"),
            cwd=str(configured_cwd),
            env={"PATH": str(configured_bin), "PATHEXT": ".cmd"},
        )
    )
    try:
        catalog = await adapter.probe_models(timeout_s=5)

        assert catalog.model_ids == ()
        assert launch_trace.read_text(encoding="utf-8") == "configured-cmd"
        assert rpc_trace.read_text(encoding="utf-8").splitlines() == [
            "connect",
            "models.list",
            "runtime.shutdown",
        ]
        assert len(windows_sdk_children) == 1
        assert windows_sdk_children[0].poll() is not None
        assert not adapter._rooms
    finally:
        await adapter.cleanup_all()


@pytest.mark.parametrize(
    ("host_node_location", "configured_node_present"),
    [("cwd", True), ("path", True), ("cwd", False)],
)
async def test_windows_javascript_uses_only_the_configured_interpreter_with_real_sdk(
    host_node_location: str,
    configured_node_present: bool,
    windows_sdk_children: list[subprocess.Popen[bytes]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_cwd = tmp_path / "host-cwd"
    host_bin = tmp_path / "host-bin"
    configured_cwd = tmp_path / "configured-cwd"
    configured_bin = configured_cwd / "bin"
    host_cwd.mkdir()
    host_bin.mkdir()
    configured_bin.mkdir(parents=True)
    node_trace = tmp_path / "launched-interpreter.txt"
    script_trace = tmp_path / "launched-script.txt"
    rpc_trace = tmp_path / "wire-methods.txt"
    script = configured_cwd / "copilot.js"
    write_rpc_peer(script, "configured-script", script_trace, rpc_trace)
    host_node = (host_cwd if host_node_location == "cwd" else host_bin) / "node.exe"
    write_node_peer(host_node, "host-node", node_trace)
    if configured_node_present:
        write_node_peer(configured_bin / "node.exe", "configured-node", node_trace)
    monkeypatch.chdir(host_cwd)
    monkeypatch.setenv("PATH", str(host_bin))
    monkeypatch.setenv("PATHEXT", ".exe")
    emulate_windows_search(monkeypatch)
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=("./copilot.js", "--acp"),
            cwd=str(configured_cwd),
            env={"PATH": "bin", "PATHEXT": ".exe;.js"},
        )
    )
    try:
        if configured_node_present:
            catalog = await adapter.probe_models(timeout_s=5)

            assert catalog.model_ids == ()
            assert node_trace.read_text(encoding="utf-8") == "configured-node"
            assert script_trace.read_text(encoding="utf-8") == "configured-script"
            assert rpc_trace.read_text(encoding="utf-8").splitlines() == [
                "connect",
                "models.list",
                "runtime.shutdown",
            ]
            assert len(windows_sdk_children) == 1
            assert windows_sdk_children[0].poll() is not None
        else:
            with pytest.raises(BandConfigError):
                await adapter.probe_models(timeout_s=5)
            assert not windows_sdk_children
            assert not node_trace.exists()
            assert not script_trace.exists()
        assert not adapter._rooms
    finally:
        await adapter.cleanup_all()
