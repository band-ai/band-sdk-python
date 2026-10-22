"""The trusted helper preserves parent lookup and isolates room import paths."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from band.integrations.codex.stdio_client import CodexStdioClient
from band.integrations.codex.winspawn import command_in_job


class TestWindowsBootstrap:
    @pytest.mark.parametrize("pythonpath", [".", "room-packages"])
    @pytest.mark.parametrize(
        "configured", [False, True], ids=["inherited", "configured"]
    )
    def test_room_pythonpath_cannot_supply_helper_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        pythonpath: str,
        configured: bool,
    ) -> None:
        workdir = tmp_path / "room"
        search = workdir / pythonpath
        search.mkdir(parents=True)
        for module in ("band", "ctypes"):
            (search / f"{module}.py").write_text(
                f"from pathlib import Path\nPath('{module}-imported').touch()\n"
                "raise SystemExit('shadowed')\n"
            )
        monkeypatch.setenv("PYTHONPATH", pythonpath)
        client = CodexStdioClient(
            command=[sys.executable, "-c", "pass"],
            cwd=str(workdir),
            env={"PYTHONPATH": pythonpath} if configured else None,
        )
        command = command_in_job(client.command, "job", 1)
        completed = subprocess.run(
            command[:4],
            cwd=workdir,
            env=client.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert not list(workdir.glob("*-imported"))
        assert completed.returncode == 2, completed.stderr

    @pytest.mark.parametrize("child_has_binary", [False, True])
    def test_child_path_does_not_change_parent_executable_selection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        child_has_binary: bool,
    ) -> None:
        parent_path = tmp_path / "parent-bin"
        parent_path.mkdir()
        executable = parent_path / "codex-test-command.exe"
        shutil.copyfile(sys.executable, executable)
        executable.chmod(0o755)
        child_path = tmp_path / "child-bin"
        child_path.mkdir()
        if child_has_binary:
            other = child_path / executable.name
            other.write_text("")
            other.chmod(0o755)
        monkeypatch.setenv("PATH", str(executable.parent))
        client = CodexStdioClient(
            command=[executable.name, "-c", "pass"], env={"PATH": str(child_path)}
        )
        command = command_in_job(client.command, "job", 1)
        selected = command[command.index("--") + 1]
        assert Path(selected).samefile(executable)
        assert client.env is not None and client.env["PATH"] == str(child_path)
