"""An interrupted meeting-demo ledger rewrite must keep unresolved ids."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.loaders import load_script_module

provision = load_script_module(
    "examples/docker_demo/provision.py", "docker_demo_provision_ledger"
)


def interrupt_uncommitted_write(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.write_text

    def write(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if data:
            self.write_bytes(b"")
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", write)


@pytest.fixture
def demo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(provision, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(provision, "AGENT_IDS", tmp_path / "agent_ids.txt")
    monkeypatch.setattr(provision, "ROOM_IDS", tmp_path / "room_ids.txt")
    monkeypatch.setattr(provision, "CONFIG_PATH", tmp_path / "agent_config.yaml")
    monkeypatch.setattr(provision, "AGENTS_ENV", tmp_path / "agents.env")
    monkeypatch.setattr(provision, "ROOM_DELETION_POLL_S", 0)
    return tmp_path


class TestUnresolvedLedgerRewrite:
    """Failed deletion keeps every id that was durable before the rewrite."""

    async def test_retry_still_deletes_an_agent_whose_rewrite_was_interrupted(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        provision.AGENT_IDS.write_text("agent-blocked\nagent-deleted\n")
        attempts = {"blocked": 0}

        async def delete_agent(agent_id: str, force: bool = True) -> None:
            del force
            if agent_id == "agent-blocked" and attempts["blocked"] == 0:
                attempts["blocked"] += 1
                raise RuntimeError("cleanup blocked")

        rest = SimpleNamespace(
            human_api_agents=SimpleNamespace(
                delete_my_agent=AsyncMock(side_effect=delete_agent)
            )
        )
        interrupt_uncommitted_write(monkeypatch)

        with pytest.raises(OSError, match="No space"):
            await provision.delete(rest)

        assert provision.AGENT_IDS.read_text(encoding="utf-8").splitlines() == [
            "agent-blocked",
            "agent-deleted",
        ]
        await provision.delete(rest)
        deleted = [
            call.args[0]
            for call in rest.human_api_agents.delete_my_agent.await_args_list
        ]
        assert deleted.count("agent-blocked") == 2
        assert not provision.AGENT_IDS.exists()

    async def test_interrupted_room_rewrite_keeps_the_undeleted_room(
        self, demo_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        del demo_dir
        provision.record_room("room-1")
        job = SimpleNamespace(
            id="job-1",
            status="failed",
            results=[SimpleNamespace(id="room-1", status="failed")],
        )
        deletions = SimpleNamespace(
            bulk_delete_my_chats=AsyncMock(return_value=SimpleNamespace(data=job)),
            show_my_bulk_deletion=AsyncMock(return_value=SimpleNamespace(data=job)),
        )
        client = SimpleNamespace(human_api_bulk_deletions=deletions)
        interrupt_uncommitted_write(monkeypatch)

        with pytest.raises(OSError, match="No space"):
            await provision.delete_rooms(client)

        assert provision.ROOM_IDS.read_text(encoding="utf-8").splitlines() == ["room-1"]
