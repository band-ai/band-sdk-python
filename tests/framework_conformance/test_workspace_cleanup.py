"""Temporary builder workspaces disappear after host unwind, even on body failure."""

from __future__ import annotations

import tempfile
from contextlib import nullcontext
from pathlib import Path

import pytest

from tests.adaptercleanup import HostKind, PassiveAdapter, host_for
from tests.e2e.baseline.toolkit.builders import _own_sandbox


class TestWorkspaceCleanup:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_failed_start_removes_its_workspace(
        self, tmp_path: Path, kind: HostKind, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandbox = tempfile.TemporaryDirectory(dir=tmp_path, prefix="failed-start-")
        path = Path(sandbox.name)
        adapter = PassiveAdapter()
        _own_sandbox(adapter, sandbox)

        async def fail_start(agent_name: str, agent_description: str) -> None:
            raise LookupError("Startup failed")

        monkeypatch.setattr(adapter, "on_started", fail_start)
        try:
            async with host_for(adapter, kind) as host:
                with pytest.raises(LookupError, match="Startup failed"):
                    await host.start()
                assert not path.exists()
        finally:
            sandbox.cleanup()

    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize(
        "fail_body", [False, True], ids=["success", "body-failure"]
    )
    async def test_host_unwind_removes_a_still_referenced_workspace(
        self, tmp_path: Path, kind: HostKind, fail_body: bool
    ) -> None:
        sandbox = tempfile.TemporaryDirectory(dir=tmp_path, prefix="builder-")
        path = Path(sandbox.name)
        (path / "session").write_text("temporary session", encoding="utf-8")
        adapter = PassiveAdapter()
        _own_sandbox(adapter, sandbox)
        try:
            expected = (
                pytest.raises(LookupError, match="Scenario failed")
                if fail_body
                else nullcontext()
            )
            with expected:
                async with host_for(adapter, kind) as host:
                    await host.start()
                    assert path.exists()
                    if fail_body:
                        raise LookupError("Scenario failed")
            assert not path.exists()
        finally:
            sandbox.cleanup()

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_failed_delete_remains_owned_for_same_host_retry(
        self, tmp_path: Path, kind: HostKind, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandbox = tempfile.TemporaryDirectory(dir=tmp_path, prefix="retry-builder-")
        path = Path(sandbox.name)
        (path / "session").write_text("temporary session", encoding="utf-8")
        remove = sandbox.cleanup
        allow_delete = False

        def delete() -> None:
            if not allow_delete:
                raise OSError("Workspace is temporarily busy")
            remove()

        monkeypatch.setattr(sandbox, "cleanup", delete)
        adapter = PassiveAdapter()
        _own_sandbox(adapter, sandbox)
        async with host_for(adapter, kind) as host:
            await host.start()
            try:
                with pytest.raises(ExceptionGroup):
                    await host.stop()
                assert (path / "session").read_text(
                    encoding="utf-8"
                ) == "temporary session"
            finally:
                allow_delete = True
                await host.stop()
                remove()
            assert not path.exists()
