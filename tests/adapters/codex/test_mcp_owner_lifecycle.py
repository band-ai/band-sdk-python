"""Owner installation and invocation lifetime never strand or cancel callers."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any

import pytest

from band.core.exceptions import InputNotAccepted
from band.core.turn_lifecycle import TurnTask
from band.integrations.codex.types import CodexSessionState
from band.runtime.cycle import TurnScope
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import make_platform_message
from tests.adapters.codex.mcpkit import (
    StatusClient,
    mcp_adapter,
    private_servers,
    start,
)
from tests.conftest import eager_tasks, requires_eager_tasks


async def invoke(adapter, tools: FakeAgentTools) -> None:
    await adapter.on_message(
        msg=make_platform_message(room_id="a"),
        tools=tools,
        history=CodexSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="a",
    )


class TestMcpOwnerLifecycle:
    @pytest.mark.parametrize(
        "eager", [False, pytest.param(True, marks=requires_eager_tasks)]
    )
    async def test_failed_registrar_unwinds_uninstalled_worker(
        self, tmp_path, eager: bool
    ) -> None:
        original = RuntimeError("registrar refused")
        workers: list[asyncio.Task[None]] = []

        class RefusingTools(FakeAgentTools):
            def register_turn_task(self, owner: TurnTask) -> None:
                workers.append(owner.task)
                raise original

        adapter, launches = mcp_adapter(tmp_path, [StatusClient()], private_servers)
        await adapter.on_started("Fixture", "Registration custody")
        try:
            with eager_tasks() if eager else nullcontext():
                with pytest.raises(RuntimeError) as error:
                    await invoke(adapter, RefusingTools())
                assert error.value is original
                await adapter.cleanup_all()
            assert workers[0].done()
            assert adapter._turn_tasks == {}
            assert adapter._turn_release == {}
            assert adapter._room_clients == {}
            assert launches == []
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.parametrize(
        "eager", [False, pytest.param(True, marks=requires_eager_tasks)]
    )
    async def test_terminating_scope_unwinds_cancelled_installation(
        self, tmp_path, eager: bool
    ) -> None:
        scope = TurnScope(control_revision=0)
        scope.terminate()
        workers: list[asyncio.Task[None]] = []

        class TerminatingTools(FakeAgentTools):
            def register_turn_task(self, owner: TurnTask) -> None:
                workers.append(owner.task)
                scope.bind(owner)

        adapter, launches = mcp_adapter(tmp_path, [StatusClient()], private_servers)
        await adapter.on_started("Fixture", "Terminating registration")
        try:
            result: BaseException | None = None
            with eager_tasks() if eager else nullcontext():
                try:
                    await invoke(adapter, TerminatingTools())
                except (asyncio.CancelledError, asyncio.InvalidStateError) as error:
                    result = error
                await adapter.cleanup_all()
            assert isinstance(result, asyncio.CancelledError)
            assert workers[0].done()
            assert adapter._turn_tasks == {}
            assert adapter._turn_release == {}
            assert adapter._room_clients == {}
            assert launches == []
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.looptime
    async def test_self_cleanup_temporary_owner_does_not_cancel_continuing_caller(
        self,
        tmp_path,
    ) -> None:
        continued = asyncio.Event()

        async def resolve(workspace: str) -> dict[str, Any]:
            await adapter.on_cleanup("a")
            return dict(await private_servers(workspace))

        adapter, launches = mcp_adapter(
            tmp_path, [StatusClient()], resolve, turn_settle_timeout_s=0.02
        )
        await adapter.on_started("Fixture", "Temporary owner lifetime")

        async def caller() -> None:
            try:
                await start(adapter)
            except (asyncio.CancelledError, InputNotAccepted):
                pass
            await asyncio.sleep(
                0.1
            )  # Continue beyond the invocation's cleanup deadline.
            continued.set()

        running = asyncio.create_task(caller())
        try:
            await asyncio.gather(running, return_exceptions=True)
            assert continued.is_set()
            assert running.cancelling() == 0
            assert launches == []
            await adapter.cleanup_all()
            assert adapter._room_clients == {}
            assert "a" not in adapter._workspaces.rooms
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.cleanup_all()
