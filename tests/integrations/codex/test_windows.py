"""Native job-object cleanup releases Windows directory handles."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from band.core.exceptions import BandConnectionError
from band.integrations.codex.stdio_client import CodexStdioClient


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows job objects")
class TestWindowsJobCleanup:
    async def test_isolated_helper_preserves_the_child_environment_and_cwd(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "room"
        workdir.mkdir()
        pythonpath = workdir / "child-packages"
        pythonpath.mkdir()
        client = CodexStdioClient(
            command=[
                sys.executable,
                "-c",
                (
                    "import pathlib, sys; "
                    "pathlib.Path('observed').write_text('\\n'.join(sys.path))"
                ),
            ],
            cwd=str(workdir),
            env={"PYTHONPATH": pythonpath.name},
        )
        try:
            await client.connect()
            assert client._proc is not None
            await client._proc.wait()
            observed = {
                Path(path).resolve()
                for path in (workdir / "observed").read_text().splitlines()
                if path
            }
            assert pythonpath.resolve() in observed
        finally:
            await client.close()

    @pytest.mark.parametrize("stage", ["empty", "child"])
    async def test_rejected_spawn_releases_helper_and_tree_directory_handles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        workdir = tmp_path / "room"
        workdir.mkdir()
        client = CodexStdioClient(
            command=[sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
            cwd=str(workdir),
        )
        command_for = client._windows_command

        def rejected_command(job_name: str, status_handle: int) -> list[str]:
            command = command_for(job_name, status_handle)
            return [
                sys.executable,
                "-P",
                str(Path(__file__).with_name("windowpeer.py")),
                stage,
                *command[4:],
            ]

        monkeypatch.setattr(client, "_windows_command", rejected_command)
        try:
            with pytest.raises(BandConnectionError):
                await client.connect()
            workdir.rmdir()
            assert not workdir.exists()
        finally:
            await client.close()
