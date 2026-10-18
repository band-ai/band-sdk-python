"""A real SDK stop must not discard an unconfirmed physical CLI process."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.core.exceptions import BandConfigError, CleanupIncompleteError
from tests.adapters import copilotwire

if TYPE_CHECKING:
    from copilot import CopilotClient

pytest.importorskip("copilot", reason="Copilot SDK extra not installed")


@dataclass
class ProcessFaults:
    """Keep actual children live while their OS termination calls fail."""

    blocked: bool = True
    children: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def intercept(
        self, child: subprocess.Popen[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.children.append(child)
        real_wait = child.wait
        real_terminate = child.terminate
        real_kill = child.kill

        def wait(timeout: float | None = None) -> int:
            if self.blocked and child.poll() is None:
                raise subprocess.TimeoutExpired(child.args, timeout)
            return real_wait(timeout=timeout)

        def terminate() -> None:
            if not self.blocked:
                real_terminate()

        def kill() -> None:
            if not self.blocked:
                real_kill()

        monkeypatch.setattr(child, "wait", wait)
        monkeypatch.setattr(child, "terminate", terminate)
        monkeypatch.setattr(child, "kill", kill)


@pytest.fixture
def process_faults(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProcessFaults]:
    """Fault Popen's process boundary, keeping the SDK lifecycle unmodified."""
    faults = ProcessFaults()
    real_init = subprocess.Popen.__init__
    peer_path = str(Path(copilotwire.__file__).resolve())

    def start(child: subprocess.Popen[bytes], *args: object, **kwargs: object) -> None:
        real_init(child, *args, **kwargs)
        if isinstance(child.args, (list, tuple)) and peer_path in child.args:
            faults.intercept(child, monkeypatch)

    monkeypatch.setattr(subprocess.Popen, "__init__", start)
    try:
        yield faults
    finally:
        faults.blocked = False
        for child in faults.children:
            if child.poll() is None:
                subprocess.Popen.kill(child)
            subprocess.Popen.wait(child, timeout=5)


@pytest.mark.parametrize(
    ("reject_connect", "expected_methods"),
    [
        (False, ("connect", "models.list", "runtime.shutdown")),
        (True, ("connect", "runtime.shutdown")),
    ],
)
async def test_failed_sdk_stop_cannot_discard_the_live_physical_child(
    tmp_path: Path,
    process_faults: ProcessFaults,
    reject_connect: bool,
    expected_methods: tuple[str, ...],
) -> None:
    trace_path = tmp_path / "wire-methods.txt"
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=(
                sys.executable,
                str(Path(copilotwire.__file__).resolve()),
                str(trace_path),
                "--reject-connect" if reject_connect else "--accept-connect",
                "--acp",
            ),
            cwd=str(tmp_path),
        )
    )
    try:
        with pytest.raises(CleanupIncompleteError):
            await adapter.probe_models(timeout_s=5)

        assert len(process_faults.children) == 1
        [child] = process_faults.children
        assert child.poll() is None
        assert tuple(trace_path.read_text(encoding="utf-8").splitlines()) == (
            expected_methods
        )

        with pytest.raises(CleanupIncompleteError):
            await adapter.cleanup_all()
        assert child.poll() is None

        process_faults.blocked = False
        async with asyncio.timeout(5):
            await adapter.cleanup_all()
        assert child.poll() is not None
        assert not adapter._rooms
    finally:
        process_faults.blocked = False
        await adapter.cleanup_all()


@pytest.mark.parametrize("sdk_version", ["1.0.4", "2.0.0", "1.1.0rc1"])
async def test_unsupported_sdk_version_is_rejected_before_starting_the_cli(
    sdk_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process_faults: ProcessFaults,
) -> None:
    actual_version = metadata.version

    def distribution_version(name: str) -> str:
        if name == "github-copilot-sdk":
            return sdk_version
        return actual_version(name)

    monkeypatch.setattr(metadata, "version", distribution_version)
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=(
                sys.executable,
                str(Path(copilotwire.__file__).resolve()),
                str(tmp_path / "wire-methods.txt"),
                "--acp",
            ),
            cwd=str(tmp_path),
        )
    )
    try:
        with pytest.raises(BandConfigError):
            await adapter.probe_models(timeout_s=5)
        assert not process_faults.children
        assert not adapter._rooms
    finally:
        process_faults.blocked = False
        await adapter.cleanup_all()


@pytest.mark.parametrize("layout", ["missing-process-field", "untyped-process-field"])
async def test_unknown_sdk_process_layout_is_rejected_before_starting_the_cli(
    layout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process_faults: ProcessFaults,
) -> None:
    module = pytest.importorskip("copilot")
    client_type = module.CopilotClient

    def unsupported_client(**options: Any) -> CopilotClient:
        client = client_type(**options)
        if layout == "missing-process-field":
            monkeypatch.delattr(client, "_process")
        else:
            monkeypatch.setattr(client, "_cli_process", object())
        return client

    monkeypatch.setattr(module, "CopilotClient", unsupported_client)
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(
            command=(
                sys.executable,
                str(Path(copilotwire.__file__).resolve()),
                str(tmp_path / "wire-methods.txt"),
                "--acp",
            ),
            cwd=str(tmp_path),
        )
    )
    try:
        with pytest.raises(BandConfigError):
            await adapter.probe_models(timeout_s=5)
        assert not process_faults.children
        assert not adapter._rooms
    finally:
        process_faults.blocked = False
        await adapter.cleanup_all()
