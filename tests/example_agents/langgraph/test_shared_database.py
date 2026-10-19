"""The actual example tools share a database until either host disposes it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.adaptercleanup import HostKind, host_for
from tests.example_agents.langgraph.kit import EXAMPLE

if TYPE_CHECKING:
    from band.adapters.langgraph import LangGraphAdapter
    from band.runtime.custom_tools import CustomToolDef


class TestSharedDatabase:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_notes_persist_but_connection_closes_at_host_stop(
        self, kind: HostKind, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        path = tmp_path / "notes.sqlite3"
        note = f"Team decision {uuid4().hex}: use 'parameterized' queries"
        async with EXAMPLE["aiosqlite"].connect(path, isolation_level=None) as database:
            async with database.execute(EXAMPLE["NOTES_SCHEMA"]):
                pass
            adapter = EXAMPLE["build_adapter"](database)
            save, read = adapter.additional_tools
            async with host_for(adapter, kind) as host:
                await host.start()
                await save[1](save[0](text=note))
                assert json.loads(await read[1](read[0]())) == [note]
                await host.stop()
                with pytest.raises(ValueError, match="no active connection"):
                    await read[1](read[0]())
        async with EXAMPLE["aiosqlite"].connect(path) as reopened:
            _, read = EXAMPLE["shared_tools"](reopened)
            assert json.loads(await read[1](read[0]())) == [note]

    async def test_host_construction_failure_closes_the_connection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = tmp_path / "agent_config.yaml"
        config.write_text(
            "langgraph_agent:\n  agent_id: test-agent\n  api_key: test-key\n"
        )
        handlers: list[CustomToolDef] = []

        def refuse(*args: object, adapter: LangGraphAdapter, **kwargs: object) -> None:
            handlers.extend(adapter.additional_tools)
            raise ValueError("Host construction failed")

        monkeypatch.setattr(EXAMPLE["Agent"], "from_config", refuse)
        settings = EXAMPLE["Settings"](
            NOTES_CONFIG=config, NOTES_DATABASE=tmp_path / "notes.sqlite3"
        )
        with pytest.raises(ValueError, match="Host construction failed"):
            await EXAMPLE["build_agent"](settings)
        _, read = handlers
        with pytest.raises(ValueError, match="no active connection"):
            await read[1](read[0]())
