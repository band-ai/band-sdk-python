"""Real Codex clients with controllable cleanup and a recorded wire boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from band.adapters import codex
from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.integrations.codex.stdio_client import CodexStdioClient
from tests.adapters.waiting import until

PROBE_MARKER = "configured-host-environment"
CLOSE_FAILURE = "probe process cleanup refused"


class ProbeClient(CodexStdioClient):
    refuse_close = False

    async def _close_process(self) -> None:
        if self.refuse_close:
            raise RuntimeError(CLOSE_FAILURE)
        await super()._close_process()


class ProbeHost:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.record = root / "probe.jsonl"
        self.clients: list[ProbeClient] = []
        self.adapters: list[CodexAdapter] = []
        self.root = root

        def build(
            *,
            command: Sequence[str] | None = None,
            cwd: str | None = None,
            env: Mapping[str, str] | None = None,
            **transport: Any,
        ) -> ProbeClient:
            client = ProbeClient(
                command=command,
                cwd=cwd,
                env=env,
                **transport,
            )
            self.clients.append(client)
            return client

        monkeypatch.setattr(codex, "CodexStdioClient", build)

    def adapter(
        self,
        *,
        replies: dict[str, Any] | None = None,
        pages: dict[str, Any] | None = None,
        endless_pages: bool = False,
        diagnostic: str | None = None,
        startup_hang: bool = False,
        **config: Any,
    ) -> CodexAdapter:
        settings = {
            "replies": replies or {},
            "endless_pages": endless_pages,
            "diagnostic": diagnostic,
            "startup_hang": startup_hang,
        }
        if pages is not None:
            settings["pages"] = pages
        adapter = CodexAdapter(
            CodexAdapterConfig(
                codex_command=(
                    sys.executable,
                    "-u",
                    str(Path(__file__).with_name("probepeer.py")),
                    str(self.record),
                    json.dumps(settings),
                ),
                codex_env={"CODEX_PROBE_MARKER": PROBE_MARKER},
                workspace_for_room=lambda room: str(self.root / room),
                **config,
            )
        )
        self.adapters.append(adapter)
        return adapter

    @property
    def messages(self) -> list[dict[str, Any]]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    @property
    def methods(self) -> list[str]:
        return [message["method"] for message in self.messages]

    async def received(self, method: str) -> None:
        await until(
            lambda: method in self.methods,
            failure=f"The app-server never received {method}",
        )

    def assert_exited(self) -> None:
        assert self.clients, "The configured app-server never started"
        for client in self.clients:
            assert client._proc is not None
            assert client._proc.returncode is not None
            assert client._windows_process is None
            assert not client._connected

    async def close(self) -> None:
        for client in self.clients:
            client.refuse_close = False
        for adapter in self.adapters:
            await adapter.cleanup_all()
        for client in self.clients:
            await client.close()
