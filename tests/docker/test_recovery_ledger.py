"""An interrupted ledger rewrite must not drop unresolved resource ids."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.loaders import load_script_module

self_registration = load_script_module(
    "examples/sandbox/self-registration/demo.py", "self_registration_recovery_ledger"
)


REST_URL = "https://band.test"


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


class TestInterruptedLedgerRewrite:
    """Cleanup keeps the previous id list when replacing it fails midway."""

    async def test_retry_still_deletes_the_id_an_interrupted_rewrite_would_drop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = "band-selfreg-demo-rewrite"
        monkeypatch.setattr(self_registration, "LEDGER_DIR", tmp_path)
        monkeypatch.setattr(
            self_registration,
            "_require_settings",
            Mock(
                return_value=SimpleNamespace(
                    endpoints=SimpleNamespace(rest_url=REST_URL)
                )
            ),
        )
        monkeypatch.setattr(self_registration, "user_rest_client", Mock())
        monkeypatch.setattr(self_registration, "_teardown_sbx", Mock(return_value=True))
        manager = SimpleNamespace(
            reap_room=AsyncMock(),
            reap_agent=AsyncMock(side_effect=[RuntimeError("cleanup blocked"), None]),
        )
        monkeypatch.setattr(
            self_registration, "ResourceManager", Mock(return_value=manager)
        )
        self_registration.record_deployment(name, REST_URL)
        self_registration.record_resource(name, "agents", "agent-1")
        interrupt_uncommitted_write(monkeypatch)

        with pytest.raises(OSError, match="No space"):
            await self_registration.cleanup_by_name(name)

        assert self_registration.recorded_resources(name, "agents") == ["agent-1"]
        await self_registration.cleanup_by_name(name)
        assert [call.args[0] for call in manager.reap_agent.await_args_list] == [
            "agent-1",
            "agent-1",
        ]
        assert not (tmp_path / name).exists()
