"""Incomplete process and reader cleanup cannot become a successful preflight."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.codex import stdio_client, winspawn
from tests.adapters.codex.probes import ProbeHost
from tests.adapters.waiting import spin


class TestProbeCleanup:
    async def test_unconfirmed_process_exit_stays_owned_for_retry(
        self, probe_host: ProbeHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = probe_host.adapter()

        async def not_observed(
            _process: asyncio.subprocess.Process, _timeout: float
        ) -> bool:
            return False

        with monkeypatch.context() as patch:
            patch.setattr(stdio_client, "await_returncode", not_observed)
            patch.setattr(winspawn, "await_returncode", not_observed)
            with pytest.raises(CleanupIncompleteError):
                await adapter.preflight(timeout_s=5)

        await adapter.cleanup_all()
        probe_host.assert_exited()

    async def test_unsettled_reader_prevents_success_and_is_rejoined(
        self, probe_host: ProbeHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = probe_host.adapter(replies={"account/read": {"hang": True}})
        probing = asyncio.create_task(adapter.preflight(timeout_s=5))
        finish_reader = asyncio.Event()
        reader_started = asyncio.Event()

        async def held_reader() -> None:
            reader_started.set()
            while not finish_reader.is_set():
                try:
                    await finish_reader.wait()
                except asyncio.CancelledError:
                    continue

        reader = asyncio.create_task(held_reader())
        try:
            await reader_started.wait()
            await probe_host.received("account/read")
            [client] = probe_host.clients
            assert client._stderr_task is not None
            client._stderr_task.cancel()
            await asyncio.gather(client._stderr_task, return_exceptions=True)
            client._stderr_task = reader
            probing.cancel()
            await spin()
            with pytest.raises(asyncio.CancelledError):
                await probing
            with monkeypatch.context() as patch:
                patch.setattr(stdio_client, "_EXIT_OBSERVE_S", 0)
                with pytest.raises(CleanupIncompleteError):
                    await adapter._harness_probe.close(timeout_s=0.1)
            finish_reader.set()
            await reader
            await adapter.cleanup_all()
            probe_host.assert_exited()
        finally:
            finish_reader.set()
            await asyncio.gather(reader, return_exceptions=True)
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
