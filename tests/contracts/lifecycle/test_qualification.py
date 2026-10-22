"""Only the qualified local host may launch retained runtime modules."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from band.contracts.errors import ContractRuntimeUnsupported
from band.contracts.models import RuntimeConfig
from band.contracts.runner import QualifiedRunner
from tests.contracts.lifecycle.kit import Spawn


class TestQualification:
    @pytest.mark.parametrize(
        "platform,version",
        [
            ("win32", [3, 12]),
            ("darwin", [3, 12]),
            ("linux", [3, 11]),
            ("linux", [3, 13]),
        ],
    )
    async def test_unqualified_host_is_rejected_before_runtime_launch(
        self, platform: str, version: list[int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawn = Spawn()
        spawn.child.stdout.feed_data(
            (
                '{"platform":"linux","version":['
                + ",".join(str(part) for part in version)
                + "]}"
            ).encode()
        )
        spawn.child.finish()
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        runner = QualifiedRunner(RuntimeConfig(python=Path(sys.executable)))
        with pytest.raises(ContractRuntimeUnsupported):
            await runner.check()
        assert spawn.calls == (1 if platform == "linux" else 0)
