"""Host probes keep resource custody through failed or interrupted cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from band.core.exceptions import BandConfigError, CleanupIncompleteError
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from tests.conftest import eager_tasks, requires_eager_tasks


class ProbeAdapter(SimpleAdapter[None]):
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: Any,
        history: None,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await tools.no_reply()


async def test_an_unsupported_preflight_cannot_report_success() -> None:
    result = await ProbeAdapter().preflight()
    assert not result.ok
    assert result.reason is not None
    assert result.remedy is not None


async def test_an_unsupported_catalog_is_not_an_empty_success() -> None:
    with pytest.raises(BandConfigError):
        await ProbeAdapter().probe_models()


async def test_failed_close_keeps_the_resource_for_a_cleanup_retry(
    tmp_path: Path,
) -> None:
    adapter = ProbeAdapter()
    resource = tmp_path / "owned-resource"
    refuse_close = True

    async def close() -> None:
        if refuse_close:
            raise PermissionError("resource still in use")
        resource.unlink()

    async def operation() -> str:
        adapter._harness_probe.own(close)
        resource.write_text("private configuration", encoding="utf-8")
        return "catalog"

    with pytest.raises(CleanupIncompleteError):
        await adapter._harness_probe.run(operation, timeout_s=0.1)
    assert resource.read_text(encoding="utf-8") == "private configuration"
    refuse_close = False
    await adapter.cleanup_all()
    assert not resource.exists()

    async def next_operation() -> str:
        return "next catalog"

    assert (
        await adapter._harness_probe.run(next_operation, timeout_s=0.1)
        == "next catalog"
    )


async def test_timeout_retains_a_start_that_settles_after_cancellation(
    tmp_path: Path,
) -> None:
    adapter = ProbeAdapter()
    resource = tmp_path / "late-resource"
    entered = asyncio.Event()
    settle = asyncio.Event()

    async def close() -> None:
        resource.unlink()

    async def operation() -> str:
        adapter._harness_probe.own(close)
        entered.set()
        try:
            await settle.wait()
        except asyncio.CancelledError:
            await settle.wait()
        resource.write_text("late configuration", encoding="utf-8")
        return "late catalog"

    running = asyncio.create_task(adapter._harness_probe.run(operation, timeout_s=0.01))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(CleanupIncompleteError):
            await running
        settle.set()
        await adapter.cleanup_all()
        assert not resource.exists()
    finally:
        settle.set()
        await asyncio.gather(running, return_exceptions=True)
        await adapter.cleanup_all()


async def test_caller_cancellation_does_not_abandon_late_startup(
    tmp_path: Path,
) -> None:
    adapter = ProbeAdapter()
    resource = tmp_path / "cancelled-resource"
    entered = asyncio.Event()
    settle = asyncio.Event()

    async def close() -> None:
        resource.unlink()

    async def operation() -> str:
        adapter._harness_probe.own(close)
        entered.set()
        try:
            await settle.wait()
        except asyncio.CancelledError:
            await settle.wait()
        resource.write_text("configuration", encoding="utf-8")
        return "catalog"

    running = asyncio.create_task(adapter._harness_probe.run(operation, timeout_s=0.01))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        settle.set()
        await adapter.cleanup_all()
        assert not resource.exists()
    finally:
        settle.set()
        await asyncio.gather(running, return_exceptions=True)
        await adapter.cleanup_all()


async def test_cancelled_cleanup_reports_no_raw_error_but_retains_retry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = ProbeAdapter()
    resource = tmp_path / "failed-resource"
    secret = "provider-token-in-transport-error"
    entered = asyncio.Event()
    release = asyncio.Event()
    failed = asyncio.Event()
    refuse_close = True

    async def close() -> None:
        entered.set()
        await release.wait()
        if refuse_close:
            failed.set()
            raise PermissionError(secret)
        resource.unlink()

    async def operation() -> str:
        adapter._harness_probe.own(close)
        resource.write_text("owned configuration", encoding="utf-8")
        return "catalog"

    running = asyncio.create_task(adapter._harness_probe.run(operation, timeout_s=1))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        release.set()
        await asyncio.wait_for(failed.wait(), 1)
        await asyncio.sleep(0)
        assert secret not in caplog.text
        assert resource.exists()
        with pytest.raises(CleanupIncompleteError):
            await adapter.cleanup_all()
        refuse_close = False
        await adapter.cleanup_all()
        assert not resource.exists()
    finally:
        refuse_close = False
        release.set()
        await asyncio.gather(running, return_exceptions=True)
        await adapter.cleanup_all()


@requires_eager_tasks
async def test_eager_execution_enrolls_resources_before_operation_start(
    tmp_path: Path,
) -> None:
    adapter = ProbeAdapter()
    resource = tmp_path / "owned"
    closed = tmp_path / "closed"

    async def close() -> None:
        resource.rename(closed)

    async def operation() -> None:
        adapter._harness_probe.own(close)
        resource.write_text("owned payload", encoding="utf-8")

    try:
        with eager_tasks():
            await adapter._harness_probe.run(operation, timeout_s=1)
        assert closed.read_text(encoding="utf-8") == "owned payload"
        assert not resource.exists()
    finally:
        await adapter.cleanup_all()
