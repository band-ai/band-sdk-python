"""Compare native executable selection with the former subprocess launch."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from band.integrations.codex.stdio_client import CodexStdioClient
from tests.integrations.codex.lookup import launch_directly


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows executable search")
class TestWindowsLaunchCompatibility:
    @pytest.mark.parametrize("path_present", [False, True], ids=["absent", "empty"])
    async def test_system_command_keeps_native_lookup_and_child_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_present: bool
    ) -> None:
        if path_present:
            monkeypatch.setenv("PATH", "")
        else:
            monkeypatch.delenv("PATH", raising=False)
        workdir = tmp_path / "room"
        workdir.mkdir()
        child_path = str(tmp_path / "child-bin")
        client = CodexStdioClient(
            command=["cmd.exe", "/d", "/c", "echo %PATH%>observed"],
            cwd=str(workdir),
            env={"PATH": child_path},
        )
        await launch_directly(client)
        observed = workdir / "observed"
        expected = observed.read_text().strip()
        observed.unlink()
        try:
            await client.connect()
            assert client._proc is not None
            await client._proc.wait()
        finally:
            await client.close()
        assert observed.read_text().strip() == expected == child_path
        observed.unlink()
        workdir.rmdir()
        assert not workdir.exists()

    async def test_application_directory_precedes_parent_cwd_and_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        workdir = tmp_path / "room"
        workdir.mkdir()
        monkeypatch.chdir(parent)
        monkeypatch.setenv("PATH", str(parent))
        name = Path(sys.executable).name
        (parent / name).write_bytes(b"not an executable")
        client = CodexStdioClient(
            command=[
                name,
                "-c",
                "import pathlib,sys; pathlib.Path('observed').write_text(sys.executable)",
            ],
            cwd=str(workdir),
        )
        await launch_directly(client)
        observed = workdir / "observed"
        expected = Path(observed.read_text())
        observed.unlink()
        try:
            await client.connect()
            assert client._proc is not None
            await client._proc.wait()
        finally:
            await client.close()
        assert Path(observed.read_text()).samefile(expected)
