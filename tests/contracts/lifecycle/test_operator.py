"""Every operator command delegates to the retained command ABI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from band.contracts.journal import ContractJournal
from band.contracts.models import RuntimeConfig
from remote_agent import cli


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

    @pytest.mark.parametrize("text", ["-error", "--after", "-"])
    async def test_journal_text_that_looks_like_an_option_reaches_the_search(
        self, text: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commands: list[tuple[str, ...]] = []
        searches: list[dict[str, Any]] = []

        async def run(self: ContractJournal, args: tuple[str, ...]) -> dict[str, Any]:
            commands.append(args)
            return {}

        def search(source: str, **page: Any) -> dict[str, Any]:
            searches.append(page)
            return {}

        monkeypatch.setattr(ContractJournal, "_run", run)
        monkeypatch.setattr(cli, "require_runtime", lambda: None)
        monkeypatch.setattr(cli.operations, "journal", search)
        operator = ContractJournal(
            Path("state"), runtime=RuntimeConfig(python=Path(sys.executable))
        )
        await operator.journal(kind=text, query=text)
        assert cli.main(list(commands[0])) == 0
        assert searches == [
            {"after": 0, "through": None, "limit": 50, "kind": text, "query": text}
        ]
