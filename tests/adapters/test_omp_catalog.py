"""Host catalog discovery uses configured CLI/native transports, never a room."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import psutil
import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.exceptions import BandConfigError, CleanupIncompleteError
from band.integrations.codex.winspawn import Job
from band.integrations.ompcatalog import OmpModelListing
from band.integrations.omprpc import OmpRpcClient
from tests.paths import REPO_ROOT


def catalog_adapter(
    tmp_path: Path,
    scenario: str,
    *,
    explicit_key: bool = False,
    mode_equals: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[OmpAdapter, Path]:
    receipts = tmp_path / "catalog-receipts.jsonl"
    config_file = tmp_path / "catalog.yml"
    config_file.write_text("{}", encoding="utf-8")
    mode = ("--mode=rpc",) if mode_equals else ("--mode", "rpc")
    adapter = OmpAdapter(
        OmpAdapterConfig(
            command=(
                sys.executable,
                "-u",
                "-m",
                "tests.integrations.ompcatalogpeer",
                *mode,
                "--config",
                str(config_file),
            ),
            cwd=str(tmp_path),
            model="openai/thinker",
            api_key="configured-provider-key",
            env={
                "OMP_CATALOG_RECEIPTS": str(receipts),
                "OMP_CATALOG_SCENARIO": scenario,
                # Python peers must remain importable from the configured workspace.
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (str(REPO_ROOT), os.environ.get("PYTHONPATH")))
                ),
                **({"OPENAI_API_KEY": "explicit-provider-key"} if explicit_key else {}),
                **(env or {}),
            },
        )
    )
    return adapter, receipts


def recorded(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def assert_processes_exited(receipts: Path) -> None:
    for pid in {record["pid"] for record in recorded(receipts)}:
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE, (
            f"Catalog subprocess {pid} survived its probe"
        )


@pytest.mark.asyncio
class TestOmpCatalog:
    @pytest.mark.parametrize("mode_equals", [False, True])
    @pytest.mark.parametrize("explicit_key", [False, True])
    async def test_cli_catalog_preserves_launcher_credentials_and_unknown_efforts(
        self, tmp_path: Path, explicit_key: bool, mode_equals: bool
    ) -> None:
        adapter, receipts = catalog_adapter(
            tmp_path, "cli", explicit_key=explicit_key, mode_equals=mode_equals
        )
        try:
            catalog = await adapter.probe_models(timeout_s=5)
            assert catalog.current_model is None
            assert [choice.model_dump() for choice in catalog.models] == [
                {
                    "id": "openai/thinker",
                    "label": "Thinking model",
                    "provider": "openai",
                    "efforts": ("low", "high"),
                    "default_effort": None,
                    "is_default": False,
                },
                {
                    "id": "local/plain",
                    "label": "Plain model",
                    "provider": "local",
                    "efforts": (),
                    "default_effort": None,
                    "is_default": False,
                },
                {
                    "id": "custom/unknown",
                    "label": "Unknown efforts",
                    "provider": "custom",
                    "efforts": None,
                    "default_effort": None,
                    "is_default": False,
                },
                {
                    "id": "custom/none",
                    "label": "No efforts",
                    "provider": "custom",
                    "efforts": (),
                    "default_effort": None,
                    "is_default": False,
                },
            ]
            records = recorded(receipts)
            [spawn] = [record for record in records if record["event"] == "spawn"]
            assert spawn["cwd"] == str(tmp_path)
            assert spawn["arguments"] == [
                "models",
                "--json",
                "--config",
                str(tmp_path / "catalog.yml"),
            ]
            assert spawn["explicit_credentials_match"] is explicit_key
            assert spawn["credentials_match"] is not explicit_key
            assert not [record for record in records if record["event"] == "rpc"]
            assert not adapter._rooms
            assert_processes_exited(receipts)
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize("scenario", ["rpc", "invalid-cli", "huge-cli"])
    async def test_native_rpc_fallback_discovers_full_catalog_without_inference(
        self, tmp_path: Path, scenario: str
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, scenario)
        try:
            catalog = await adapter.probe_models(timeout_s=15)
            assert catalog.model_ids == (
                "openai/thinker",
                "local/plain",
                "custom/unknown",
            )
            assert catalog.current_model == "openai/thinker"
            thinker = catalog.choice("openai/thinker")
            assert thinker is not None
            assert thinker.provider == "openai"
            assert thinker.efforts == ("off", "low", "high")
            assert thinker.default_effort == "low"
            assert not thinker.is_default
            assert catalog.choice("local/plain").efforts == ()
            assert catalog.choice("custom/unknown").efforts is None
            commands = [
                record["command"]
                for record in recorded(receipts)
                if record["event"] == "rpc"
            ]
            assert "get_available_models" in commands
            assert "get_state" in commands
            assert "get_available_thinking_levels" in commands
            assert not {"prompt", "new_session", "set_host_tools"}.intersection(
                commands
            )
            assert all(
                record["credentials_match"]
                for record in recorded(receipts)
                if record["event"] == "spawn"
            )
            assert not adapter._rooms
            assert_processes_exited(receipts)
            second = await adapter.probe_models(timeout_s=5)
            assert second == catalog
            assert_processes_exited(receipts)
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize("scenario", ["empty-cli", "empty-rpc"])
    async def test_advertised_empty_catalog_is_success_not_unsupported(
        self, tmp_path: Path, scenario: str
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, scenario)
        try:
            catalog = await adapter.probe_models(timeout_s=5)
            assert catalog.models == ()
            assert_processes_exited(receipts)
            if scenario == "empty-cli":
                assert not [
                    record for record in recorded(receipts) if record["event"] == "rpc"
                ]
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize("scenario", ["unsupported", "malformed-rpc"])
    async def test_unsupported_or_malformed_rpc_never_becomes_empty_success(
        self, tmp_path: Path, scenario: str
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, scenario)
        try:
            with pytest.raises(BandConfigError, match="OMP.*catalog"):
                await adapter.probe_models(timeout_s=5)
            assert_processes_exited(receipts)
            assert not adapter._rooms
        finally:
            await adapter.cleanup_all()

    async def test_cancelled_cli_spawn_is_physically_closed_before_return(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        adapter, _receipts = catalog_adapter(tmp_path, "blocked-cli")
        original_spawn = asyncio.create_subprocess_exec
        spawned = asyncio.Event()
        processes: list[asyncio.subprocess.Process] = []

        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            processes.append(process)
            spawned.set()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        running = asyncio.create_task(adapter.probe_models(timeout_s=5))
        try:
            async with asyncio.timeout(5):
                await spawned.wait()
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            assert processes
            assert all(process.returncode is not None for process in processes)
            assert not adapter._rooms
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.cleanup_all()

    async def test_native_cleanup_failure_stays_owned_for_adapter_cleanup_retry(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, "rpc")
        original_close = OmpRpcClient.close
        failed = False

        async def close(client: OmpRpcClient) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("native close temporarily refused")
            await original_close(client)

        monkeypatch.setattr(OmpRpcClient, "close", close)
        try:
            with pytest.raises(CleanupIncompleteError):
                await adapter.probe_models(timeout_s=5)
            records = recorded(receipts)
            native_pid = [
                record["pid"] for record in records if record["event"] == "spawn"
            ][-1]
            assert psutil.pid_exists(native_pid)
            await adapter.cleanup_all()
            assert_processes_exited(receipts)
            assert (await adapter.probe_models(timeout_s=5)).model_ids
            assert_processes_exited(receipts)
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize(
        "scenario", ["blocked-cli", "blocked-cli-descendant", "blocked-rpc"]
    )
    async def test_probe_timeout_physically_closes_the_started_transport(
        self, tmp_path: Path, scenario: str
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, scenario)
        try:
            with pytest.raises(TimeoutError):
                await adapter.probe_models(timeout_s=2)
            assert_processes_exited(receipts)
            assert not adapter._rooms
        finally:
            await adapter.cleanup_all()

    async def test_concurrent_probes_close_previous_process_before_next_spawn(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, "cli")
        original_spawn = asyncio.create_subprocess_exec
        processes: list[asyncio.subprocess.Process] = []

        async def spawn(*args, **kwargs):
            assert all(process.returncode is not None for process in processes)
            process = await original_spawn(*args, **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        try:
            first, second = await asyncio.gather(
                adapter.probe_models(timeout_s=5), adapter.probe_models(timeout_s=5)
            )
            assert first.model_ids == second.model_ids
            assert len(processes) == 2
            assert_processes_exited(receipts)
        finally:
            await adapter.cleanup_all()

    async def test_cancelled_fallback_cannot_finish_while_process_cleanup_is_blocked(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        adapter, receipts = catalog_adapter(tmp_path, "huge-cli")
        loop = asyncio.get_running_loop()
        killing = asyncio.Event()
        rejoined = asyncio.Event()
        release_kill = threading.Event()
        first_worker_done = threading.Event()
        release_job = asyncio.Event()
        close_calls = 0
        original_kill = os.killpg if os.name != "nt" else None
        original_job_wait = Job.wait_until_idle
        original_close = OmpModelListing.close

        def gated_kill(group_id: int, sig: int) -> None:
            first_attempt = not killing.is_set()
            try:
                if first_attempt:
                    loop.call_soon_threadsafe(killing.set)
                    if not release_kill.wait(15):
                        raise RuntimeError(
                            "The test did not release the process-kill barrier"
                        )
                assert original_kill is not None
                original_kill(group_id, sig)
            finally:
                if first_attempt:
                    first_worker_done.set()

        async def gated_job_wait(job: Job, timeout: float) -> None:
            first_attempt = not killing.is_set()
            try:
                if first_attempt:
                    killing.set()
                    await release_job.wait()
                await original_job_wait(job, timeout)
            finally:
                if first_attempt:
                    first_worker_done.set()

        async def observed_close(listing: OmpModelListing) -> None:
            nonlocal close_calls
            close_calls += 1
            if close_calls == 2:
                rejoined.set()
            await original_close(listing)

        if os.name == "nt":
            monkeypatch.setattr(Job, "wait_until_idle", gated_job_wait)
        else:
            monkeypatch.setattr(os, "killpg", gated_kill)
        monkeypatch.setattr(OmpModelListing, "close", observed_close)
        running = asyncio.create_task(adapter.probe_models(timeout_s=15))
        cleanup: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout(15):
                await killing.wait()
                running.cancel()
                await rejoined.wait()
                cleanup = asyncio.create_task(adapter.cleanup_all())
                with pytest.raises(CleanupIncompleteError):
                    await adapter._harness_probe.close(timeout_s=1)
                assert not cleanup.done()
                assert not running.done()
                release_kill.set()
                release_job.set()
                with pytest.raises(asyncio.CancelledError):
                    await running
                await cleanup
            assert_processes_exited(receipts)
            assert not adapter._rooms
        finally:
            release_kill.set()
            release_job.set()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            await adapter.cleanup_all()
            if killing.is_set():
                assert await asyncio.to_thread(first_worker_done.wait, 5)
