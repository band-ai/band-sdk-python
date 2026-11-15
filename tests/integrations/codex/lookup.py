"""Filesystem-backed peer for the native executable-search boundary."""

from __future__ import annotations

import asyncio
import ctypes
import os
from pathlib import Path
from types import SimpleNamespace

from band.integrations.codex.stdio_client import CodexStdioClient
from band.integrations.codex.winspawn import command_in_job


async def launch_directly(client: CodexStdioClient) -> None:
    process = await asyncio.create_subprocess_exec(
        *client.command,
        cwd=client.cwd,
        env=client.env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode(errors="replace")


def selected_executable(name: str) -> Path:
    command = command_in_job([name], "test-job", 1)
    return Path(command[command.index("--") + 1])


class WindowsLookup:
    def __init__(self, root: Path) -> None:
        self.application = root / "application"
        self.current = root / "current"
        self.system = root / "system"
        self.windows = root / "windows"
        self.path = root / "path"
        self.directories = (
            self.application,
            self.current,
            self.system,
            self.windows / "System",
            self.windows,
            self.path,
        )
        for directory in self.directories:
            directory.mkdir(parents=True, exist_ok=True)
        self.image = self.application / "sdk.exe"
        self.image.touch()
        self.parent_path: str | None = str(self.path)
        self.search_current = True
        self.kernel = SimpleNamespace(
            GetModuleFileNameW=lambda _module, buffer, size: self.copy(
                str(self.image), buffer, size
            ),
            GetSystemDirectoryW=lambda buffer, size: self.copy(
                str(self.system), buffer, size
            ),
            GetWindowsDirectoryW=lambda buffer, size: self.copy(
                str(self.windows), buffer, size
            ),
            GetEnvironmentVariableW=lambda _name, buffer, size: self.copy(
                self.parent_path or "", buffer, size
            ),
            NeedCurrentDirectoryForExePathW=lambda _name: self.search_current,
            SearchPathW=self.search,
        )

    @staticmethod
    def copy(value: str, buffer: ctypes.Array[ctypes.c_wchar], size: int) -> int:
        if len(value) >= size:
            return len(value) + 1
        buffer.value = value
        return len(value)

    def search(
        self,
        path: str,
        name: str,
        extension: str,
        size: int,
        buffer: ctypes.Array[ctypes.c_wchar],
        file_part: object,
    ) -> int:
        filename = name if Path(name).suffix else name + extension
        for directory in path.split(os.pathsep):
            candidate = Path(directory) / filename
            if candidate.is_file():
                return self.copy(str(candidate), buffer, size)
        return 0
