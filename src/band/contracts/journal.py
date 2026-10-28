"""Complete async operator API over the retained nonexecuting journal commands."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from band.contracts.models import JournalQuery, RuntimeConfig
from band.contracts.runner import QualifiedRunner
from band.contracts.wire import JsonObject, decode_journal


class ContractJournal:
    """Delegate verified storage operations to the installed runner process."""

    def __init__(self, source: Path, *, runtime: RuntimeConfig) -> None:
        self.source = source
        self._runner = QualifiedRunner(runtime)

    async def _run(self, arguments: tuple[str, ...]) -> JsonObject:
        return await self._runner.execute(
            "remote_agent.cli",
            arguments,
            allowed=(0, 3) if arguments[0] == "replay" else (0,),
            decoder=decode_journal,
        )

    async def inspect(self) -> JsonObject:
        return await self._run(("inspect", str(self.source)))

    async def journal(
        self,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int = 50,
        kind: str | None = None,
        query: str = "",
    ) -> JsonObject:
        page = JournalQuery(
            after=after, through=through, limit=limit, kind=kind, query=query
        )
        arguments = (
            "journal",
            str(self.source),
            "--after",
            str(page.after),
            "--limit",
            str(page.limit),
        )
        if page.through is not None:
            arguments += ("--through", str(page.through))
        # One token per text option: a value that starts with "-" stays a value.
        if page.kind is not None:
            arguments += ("--kind=" + page.kind,)
        if page.query:
            arguments += ("--query=" + page.query,)
        return await self._run(arguments)

    async def backup(self, destination: Path) -> JsonObject:
        return await self._run(("backup", str(self.source), str(destination)))

    async def restore(self, destination: Path) -> JsonObject:
        """Restore the verified source backup into a new destination."""
        return await self._run(("restore", str(self.source), str(destination)))

    async def export(self, destination: Path) -> JsonObject:
        return await self._run(("export", str(self.source), str(destination)))

    async def replay(
        self,
        identifier: str,
        destination: Path,
        *,
        kind: Literal["program", "model"] = "program",
    ) -> JsonObject:
        if not identifier or kind not in ("program", "model"):
            raise ValueError(
                "Provide the invocation identifier and program or model kind"
            )
        return await self._run(
            ("replay", str(self.source), identifier, str(destination), "--kind", kind)
        )

    async def rotate_host_key(self) -> JsonObject:
        return await self._run(("rotate-host-key", str(self.source)))
