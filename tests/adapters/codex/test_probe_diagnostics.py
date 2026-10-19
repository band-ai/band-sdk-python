"""Standalone probes retain failures without copying raw transport diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from band.integrations.codex.stdio_client import CodexStdioClient
from tests.adapters.codex.kit import send_bootstrap
from tests.adapters.codex.probes import ProbeHost
from tests.adapters.waiting import spin

SENSITIVE_DIAGNOSTIC = "configured-token-that-must-not-reach-probe-logs"


class TestProbeDiagnostics:
    async def test_real_probe_wire_and_stderr_are_metadata_only(
        self, probe_host: ProbeHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = probe_host.adapter(diagnostic=SENSITIVE_DIAGNOSTIC)

        with caplog.at_level(logging.DEBUG):
            result = await adapter.preflight(timeout_s=5)

        assert result.ok
        assert "initialize" in probe_host.methods
        assert "account/read" in probe_host.methods
        probe_host.assert_exited()
        assert SENSITIVE_DIAGNOSTIC not in caplog.text

    async def test_room_wire_and_stderr_are_also_metadata_only(
        self, probe_host: ProbeHost, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = probe_host.adapter(
            diagnostic=SENSITIVE_DIAGNOSTIC, model="configured-model"
        )
        await adapter.on_started("Codex Agent", "A coding agent")
        with caplog.at_level(logging.DEBUG):
            await send_bootstrap(adapter, content="/model")
            await adapter.cleanup_all()

        assert "initialize" in probe_host.methods
        probe_host.assert_exited()
        assert SENSITIVE_DIAGNOSTIC not in caplog.text

    @pytest.mark.parametrize("request_id", [None, 7], ids=["notification", "request"])
    async def test_full_transport_queue_logs_only_the_dropped_event_kind(
        self, caplog: pytest.LogCaptureFixture, request_id: int | None
    ) -> None:
        client = CodexStdioClient()
        client._events = asyncio.Queue(maxsize=1)
        payload = {"method": SENSITIVE_DIAGNOSTIC, "id": request_id}

        with caplog.at_level(logging.WARNING):
            await client._dispatch_rpc_message(json.dumps(payload))
            await client._dispatch_rpc_message(json.dumps(payload))

        event = await client.recv_event(timeout_s=1)
        assert event.method == SENSITIVE_DIAGNOSTIC
        assert event.kind == ("notification" if request_id is None else "request")
        assert any(record.levelno == logging.WARNING for record in caplog.records)
        assert event.kind in caplog.text
        assert SENSITIVE_DIAGNOSTIC not in caplog.text

    async def test_late_close_failure_keeps_the_original_error_and_retry_without_raw_log(
        self,
        probe_host: ProbeHost,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = probe_host.adapter(replies={"account/read": {"hang": True}})
        probing = asyncio.create_task(adapter.preflight(timeout_s=5))
        close_started = asyncio.Event()
        finish_close = asyncio.Event()

        async def refused_close() -> None:
            close_started.set()
            await finish_close.wait()
            raise RuntimeError(SENSITIVE_DIAGNOSTIC)

        try:
            with caplog.at_level(logging.DEBUG):
                await probe_host.received("account/read")
                [client] = probe_host.clients
                with monkeypatch.context() as patch:
                    patch.setattr(client, "_close_process", refused_close)
                    probing.cancel()
                    await asyncio.wait_for(close_started.wait(), timeout=5)
                    probing.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await probing
                    finish_close.set()
                    assert client._close_task is not None
                    with pytest.raises(RuntimeError, match=SENSITIVE_DIAGNOSTIC):
                        await client._close_task
                    await spin()
                    assert client._proc is not None
                    assert client._proc.returncode is None
                assert SENSITIVE_DIAGNOSTIC not in caplog.text
        finally:
            finish_close.set()
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
            await adapter.cleanup_all()
        probe_host.assert_exited()
