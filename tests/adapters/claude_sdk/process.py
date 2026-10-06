"""A real Python child behind the SDK's subprocess transport."""

from __future__ import annotations

import sys

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

PROBE = """
import json
import sys
from pathlib import Path

Path("probe.txt").write_text(sys.argv[1])
sys.stdout.write(json.dumps({"cwd": str(Path.cwd()), "marker": sys.argv[1]}) + chr(10))
sys.stdout.flush()
sys.stdin.read()
"""


class WorkspaceProcess(SubprocessCLITransport):
    """Exercise SDK spawning, pipes and cleanup without a Claude installation."""

    refuse_close = False

    async def close(self) -> None:
        if self.refuse_close:
            raise RuntimeError("subprocess cleanup failed")
        await super().close()

    async def _check_claude_version(self) -> None:
        pass

    def _build_command(self) -> list[str]:
        return [sys.executable, "-u", "-c", PROBE, self._options.env["PROBE_MARKER"]]

    @property
    def exited(self) -> bool:
        return self._child.returncode is not None

    async def connect(self) -> None:
        await super().connect()
        assert self._process is not None
        self._child = self._process
