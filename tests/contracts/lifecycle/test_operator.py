"""Every operator command delegates to the retained command ABI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from band.contracts.journal import ContractJournal
from band.contracts.models import RuntimeConfig


class TestOperator:
    @pytest.mark.parametrize(
        "operation,arguments,expected",
        [
            ("inspect", (), ("inspect", "state")),
            ("journal", (), ("journal", "state", "--after", "0", "--limit", "50")),
            ("backup", (Path("backup"),), ("backup", "state", "backup")),
            ("restore", (Path("backup"),), ("restore", "state", "backup")),
            ("export", (Path("export"),), ("export", "state", "export")),
            (
                "replay",
                ("record", Path("replay")),
                ("replay", "state", "record", "replay", "--kind", "program"),
            ),
            ("rotate_host_key", (), ("rotate-host-key", "state")),
        ],
    )
    async def test_commands_preserve_operator_arguments(
        self,
        operation: str,
        arguments: tuple[Any, ...],
        expected: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, ...]] = []

        async def run(self: ContractJournal, args: tuple[str, ...]) -> dict[str, Any]:
            calls.append(args)
            return {"replayable": False}

        monkeypatch.setattr(ContractJournal, "_run", run)
        operator = ContractJournal(
            Path("state"), runtime=RuntimeConfig(python=Path(sys.executable))
        )
        result = await getattr(operator, operation)(*arguments)
        assert result == {"replayable": False}
        assert calls == [expected]
