"""Who owns a Claude connection while its MCP servers resolve: the room's
startup, until it publishes or settles. The CLI is the scripted fake behind
the real SDK client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.integrations.claude_sdk.mcp_startup import ClaudeMcpStartupError
from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManager,
    ClaudeSessionManagerStoppedError,
)
from band.runtime.tools import BAND_MCP_SERVER_NAME
from band.workspaces import RoomWorkspaces
from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import (
    lease_client,
    refuse_mcp_removal,
)
from tests.adapters.claude_sdk.mcpkit import (
    BAND,
    SECRET,
    ClientCustody,
    CountingResolver,
    FailingBand,
    HeldResolver,
    HeldServers,
    RaisingTruth,
    ReuseProbe,
    SettlingResolver,
    manager_for,
    outdated_session,
)


class TestMcpStartupOwnership:
    async def test_concurrent_callers_share_one_resolution(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        resolver = HeldResolver()
        manager = manager_for(tmp_path, resolver, claude)
        try:
            first = asyncio.create_task(lease_client(manager, "room-a"))
            second = asyncio.create_task(lease_client(manager, "room-a"))
            await resolver.entered.wait()
            resolver.release.set()
            a, b = await asyncio.gather(first, second)
            assert a is b
            assert len(resolver.calls) == 1
            assert len(claude.sessions) == 1
        finally:
            resolver.release.set()
            await manager.stop()

    async def test_a_held_resolution_never_blocks_another_room(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        held = HeldResolver()

        async def resolver(workspace: str) -> Mapping[str, Any]:
            if workspace.endswith("room-a"):
                return await held(workspace)
            return {}

        manager = manager_for(tmp_path, resolver, claude)
        try:
            blocked = asyncio.create_task(lease_client(manager, "room-a"))
            await held.entered.wait()
            other = await lease_client(manager, "room-b")
            await manager.cleanup_session("room-b")
            assert not blocked.done()
            assert other is not None
        finally:
            held.release.set()
            await asyncio.gather(blocked, return_exceptions=True)
            await manager.stop()

    async def test_the_last_waiter_leaving_retires_the_startup_without_a_late_client(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        resolver = HeldResolver(swallow=True)
        manager = manager_for(tmp_path, resolver, claude)
        try:
            staying = asyncio.create_task(lease_client(manager, "room-a"))
            leaving = asyncio.create_task(lease_client(manager, "room-a"))
            await resolver.entered.wait()
            leaving.cancel()
            await asyncio.gather(leaving, return_exceptions=True)
            # One waiter still wants it: nothing was cancelled.
            assert resolver.cancellations == 0
            staying.cancel()
            while resolver.cancellations == 0:
                await asyncio.sleep(0)
            assert not staying.done()
            # The callback swallows that one cancellation and keeps going; the room
            # keeps its claim until it settles, and nothing launches afterwards.
            assert bool(manager._rooms)
            resolver.release.set()
            await resolver.settled.wait()
            await asyncio.gather(staying, return_exceptions=True)
            await manager.cleanup_session("room-a")
            assert resolver.cancellations == 1
            assert claude.sessions == []
            assert not bool(manager._rooms)
        finally:
            resolver.release.set()
            await manager.stop()

    async def test_a_request_cancelled_before_starting_claims_nothing(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        resolver = HeldResolver()
        manager = manager_for(tmp_path, resolver, claude)
        try:
            request = asyncio.create_task(lease_client(manager, "room-a"))
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            # No room or resolver is entered by the cancelled operation.
            await manager.cleanup_session("room-other")
            assert resolver.calls == []
            assert not bool(manager._rooms)
        finally:
            await manager.stop()

    @pytest.mark.parametrize("swallow", [False, True])
    async def test_cleanup_retires_a_resolving_startup_and_holds_the_room_until_it_settles(
        self, tmp_path: Path, claude: FakeClaude, swallow: bool
    ) -> None:
        resolver = HeldResolver(swallow=swallow)
        manager = manager_for(tmp_path, resolver, claude)
        try:
            waiting = asyncio.create_task(lease_client(manager, "room-a"))
            await resolver.entered.wait()
            cleanup = asyncio.create_task(manager.cleanup_session("room-a"))
            arrival = asyncio.create_task(lease_client(manager, "room-a"))
            await asyncio.wait((cleanup,), timeout=0)
            if swallow:
                assert not cleanup.done()
                resolver.release.set()
            await cleanup
            with pytest.raises((ClaudeMcpStartupError, asyncio.CancelledError)):
                await waiting
            # Closing admission refuses arrivals; the host can redeliver after
            # the retained disposal has released the previous room.
            with pytest.raises(RoomClosingError):
                await arrival
            assert resolver.cancellations == 1
            resolver.release.set()
            client = await lease_client(manager, "room-a")
            assert len(resolver.calls) == 2
            assert len(claude.sessions) == 1
            assert claude.sessions[0].alive and client is not None
        finally:
            resolver.release.set()
            await manager.stop()

    async def test_a_resolver_cleaning_up_its_own_room_does_not_wait_on_itself(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager_ref: list[ClaudeSessionManager] = []
        returned = asyncio.Event()

        async def resolver(workspace: str) -> Mapping[str, Any]:
            with pytest.raises(CleanupIncompleteError):
                await manager_ref[0].cleanup_session("room-a")
            returned.set()
            return {}

        manager = manager_for(tmp_path, resolver, claude)
        manager_ref.append(manager)
        try:
            with pytest.raises((ClaudeMcpStartupError, asyncio.CancelledError)):
                async with asyncio.timeout(5):
                    await lease_client(manager, "room-a")
            assert returned.is_set()
            assert claude.sessions == []
            async with asyncio.timeout(5):
                while bool(manager._rooms):
                    await asyncio.sleep(0)
        finally:
            await manager.stop()

    async def test_a_resolver_stopping_the_manager_does_not_wait_on_itself(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager_ref: list[ClaudeSessionManager] = []

        async def resolver(workspace: str) -> Mapping[str, Any]:
            with pytest.raises(CleanupIncompleteError):
                await manager_ref[0].stop()
            return {}

        manager = manager_for(tmp_path, resolver, claude)
        manager_ref.append(manager)
        with pytest.raises((ClaudeSessionManagerStoppedError, asyncio.CancelledError)):
            async with asyncio.timeout(5):
                await lease_client(manager, "room-a")
        async with asyncio.timeout(5):
            await manager.stop()
        assert claude.sessions == []
        assert not bool(manager._rooms)

    async def test_stop_wins_over_a_startup_finishing_late(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        resolver = HeldResolver(swallow=True)
        manager = manager_for(tmp_path, resolver, claude)
        waiting = asyncio.create_task(lease_client(manager, "room-a"))
        await resolver.entered.wait()
        stopping = asyncio.create_task(manager.stop())
        await asyncio.wait((stopping,), timeout=0)
        resolver.release.set()
        await stopping
        with pytest.raises((ClaudeSessionManagerStoppedError, asyncio.CancelledError)):
            await waiting
        assert claude.sessions == []
        assert not bool(manager._rooms)

    async def test_a_failed_close_keeps_the_file_and_claim_until_a_retry(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        async def resolver(workspace: str) -> Mapping[str, Any]:
            return {}

        manager = manager_for(tmp_path, resolver, claude)
        try:
            await lease_client(manager, "room-a")
            [session] = claude.sessions
            path = Path(str(session.options.mcp_servers))
            claude.refuse_close = True
            with pytest.raises(ClaudeMcpStartupError, match="cleanup"):
                await manager.cleanup_session("room-a")
            assert path.exists()
            assert bool(manager._rooms)

            claude.refuse_close = False
            await manager.cleanup_session("room-a")
            assert not path.exists()
            assert not bool(manager._rooms)
        finally:
            claude.refuse_close = False
            await manager.stop()

    @pytest.mark.parametrize(
        "retire_own_room", [False, True], ids=["self-cancel", "self-cleanup"]
    )
    async def test_a_settling_startup_is_never_cancelled_again(
        self, tmp_path: Path, claude: FakeClaude, retire_own_room: bool
    ) -> None:
        resolver = SettlingResolver(retire_own_room=retire_own_room)
        manager = manager_for(tmp_path, resolver, claude)
        resolver.manager = manager
        try:
            waiting = asyncio.create_task(lease_client(manager, "room-a"))
            await resolver.settling.wait()
            # An outside cleanup takes the startup over while it settles.
            cleanup = asyncio.create_task(manager.cleanup_session("room-a"))
            await asyncio.sleep(0)
            resolver.release.set()
            await cleanup
            with pytest.raises((ClaudeMcpStartupError, asyncio.CancelledError)):
                await waiting
            assert resolver.log == ["settled"]
            assert claude.sessions == []
            assert not bool(manager._rooms)
        finally:
            resolver.release.set()
            await manager.stop()

    async def test_a_replacement_whose_caller_left_during_the_old_close_never_publishes(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager, resolver, _ = await outdated_session(tmp_path, claude)
        hold = claude.closing = Hold()
        try:
            replacing = asyncio.create_task(lease_client(manager, "room-a"))
            await hold.reached.wait()  # the old client's close is in progress
            replacing.cancel()
            await asyncio.gather(replacing, return_exceptions=True)
            hold.released.set()
            await manager.cleanup_session("room-a")

            # The replacement was retired: never resolved, launched or published.
            assert len(resolver.calls) == 1
            assert len(claude.sessions) == 1
            assert not claude.sessions[0].alive
            assert not bool(manager._rooms)
        finally:
            hold.released.set()
            claude.closing = None
            await manager.stop()

    async def test_stop_during_a_replacements_old_close_leaves_nothing_behind(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager, resolver, _ = await outdated_session(tmp_path, claude)
        hold = claude.closing = Hold()
        try:
            replacing = asyncio.create_task(lease_client(manager, "room-a"))
            await hold.reached.wait()
            stopping = asyncio.create_task(manager.stop())
            hold.released.set()
            await stopping
            with pytest.raises(RoomClosingError):
                await replacing
            await manager.stop()

            assert len(resolver.calls) == 1
            assert len(claude.sessions) == 1
            assert not bool(manager._rooms)
        finally:
            hold.released.set()
            claude.closing = None

    async def test_a_resolver_that_swallows_its_own_cancellation_publishes_nothing(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        async def resolver(workspace: str) -> Mapping[str, Any]:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass  # caught, never uncancelled, and a result returned anyway
            return {}

        manager = manager_for(tmp_path, resolver, claude)
        try:
            with pytest.raises(asyncio.CancelledError):
                await lease_client(manager, "room-a")
            assert claude.sessions == []
            assert not manager.has_session("room-a")
        finally:
            await manager.stop()

    async def test_a_finished_keep_claim_retirement_does_not_stall_a_releasing_cleanup(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager = manager_for(tmp_path, CountingResolver(), claude)
        await lease_client(manager, "room-a")
        try:
            session = manager.room("room-a")
            assert session.recover("retire before final disposal")

            await manager.cleanup_session("room-a")

            assert not bool(manager._rooms)
        finally:
            await manager.stop()

    async def test_a_file_that_cannot_be_removed_stays_owned_until_a_retry(
        self, tmp_path: Path, claude: FakeClaude
    ) -> None:
        manager = manager_for(tmp_path, CountingResolver(), claude)
        await lease_client(manager, "room-a")
        path = Path(str(claude.sessions[0].options.mcp_servers))
        try:
            with refuse_mcp_removal(path):
                with pytest.raises(ClaudeMcpStartupError, match="cleanup"):
                    await manager.cleanup_session("room-a")
                assert path.exists()
                assert bool(manager._rooms)

            await manager.cleanup_session("room-a")
            assert not path.exists()
            assert not bool(manager._rooms)
        finally:
            await manager.stop()

    async def test_a_close_failure_reports_no_native_text(
        self, tmp_path: Path, claude: FakeClaude, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = manager_for(tmp_path, CountingResolver(), claude)
        await lease_client(manager, "room-a")
        [session] = claude.sessions
        real_close = session.close

        async def leaking_close() -> None:
            raise OSError(f"close failed with {SECRET}")

        session.close = leaking_close  # type: ignore[method-assign]
        try:
            with (
                caplog.at_level(logging.DEBUG),
                pytest.raises(ClaudeMcpStartupError) as caught,
            ):
                await manager.cleanup_session("room-a")
            rendered = [str(caught.value), repr(caught.value), caplog.text]
            assert caught.value.__cause__ is None and caught.value.__suppress_context__
            assert not any(SECRET in text for text in rendered)
            assert bool(manager._rooms)
        finally:
            session.close = real_close  # type: ignore[method-assign]
            await manager.stop()

    @pytest.mark.parametrize("retained", ["file", "child"])
    async def test_a_failed_reservation_keeps_what_the_room_still_holds(
        self,
        tmp_path: Path,
        claude: FakeClaude,
        caplog: pytest.LogCaptureFixture,
        retained: str,
    ) -> None:
        resolver = CountingResolver()
        band = FailingBand()
        claude.mcp_statuses.append(
            [{"name": BAND_MCP_SERVER_NAME, "status": "connected"}]
        )
        manager = ClaudeSessionManager(
            ClaudeAgentOptions(),
            mcp_servers_factory=band,
            workspaces=RoomWorkspaces(lambda room_id: str(tmp_path / room_id)),
            resolve_mcp_servers=resolver,
        )
        await lease_client(manager, "room-a")
        path = Path(str(claude.sessions[0].options.mcp_servers))
        refused_removal = contextlib.ExitStack()
        try:
            # The room's cleanup fails, leaving its file (or its whole child) held.
            if retained == "file":
                refused_removal.enter_context(refuse_mcp_removal(path))
            else:
                claude.refuse_close = True
            with pytest.raises(ClaudeMcpStartupError, match="cleanup"):
                session = manager.room("room-a")
                session.recover("retire before replacement")
                await lease_client(manager, "room-a")
            if retained == "file":
                refused_removal.close()
            else:
                claude.refuse_close = False

            band.failing = True
            with (
                caplog.at_level(logging.DEBUG),
                pytest.raises(ClaudeMcpStartupError) as caught,
            ):
                await lease_client(manager, "room-a")
            assert caught.value.stage == "reservation"
            assert SECRET not in str(caught.value) and SECRET not in caplog.text
            # Nothing new was resolved, allocated or spawned, and the old custody
            # is still owned.
            assert len(resolver.calls) == 1
            assert len(claude.sessions) == 1
            assert path.exists()
            assert bool(manager._rooms)

            band.failing = False
            await manager.cleanup_session("room-a")
            assert not path.exists()
            assert not bool(manager._rooms)
            await lease_client(manager, "room-a")
            assert len(claude.sessions) == 2
        finally:
            refused_removal.close()
            claude.refuse_close = False
            band.failing = False
            await manager.stop()
        assert not bool(manager._rooms)

    @pytest.mark.parametrize(
        "phase", ["reservation", "reuse", "pre-launch", "construction", "post-status"]
    )
    async def test_a_callback_closing_admission_stops_everything_after_it(
        self,
        tmp_path: Path,
        claude: FakeClaude,
        monkeypatch: pytest.MonkeyPatch,
        phase: str,
    ) -> None:
        """A callback the manager runs closes its admission mid-startup: nothing
        is constructed, reused or published after that."""
        owned_tmp = tmp_path / "tmp"
        owned_tmp.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(owned_tmp))
        armed = {"on": phase == "reservation"}
        manager_ref: list[ClaudeSessionManager] = []
        resolutions: list[str] = []

        def band(room_id: str) -> dict[str, Any]:
            if armed["on"] or (phase == "post-status" and claude.status_requests > 0):
                manager_ref[0].close_admission()
            return dict(BAND)

        async def resolver(workspace: str) -> Mapping[str, Any]:
            resolutions.append(workspace)
            if phase == "pre-launch":
                armed["on"] = True
            return {}

        async def allow(*args: Any) -> Any:
            return None

        def permissions(session: Any, generation: int) -> Any:
            if phase == "construction" and resolutions:
                manager_ref[0].close_admission()
            return allow

        claude.mcp_statuses.append(
            [{"name": BAND_MCP_SERVER_NAME, "status": "connected"}]
        )
        manager = ClaudeSessionManager(
            ClaudeAgentOptions(),
            can_use_tool_factory=permissions,
            mcp_servers_factory=band,
            workspaces=RoomWorkspaces(lambda room_id: str(tmp_path / room_id)),
            resolve_mcp_servers=resolver,
        )
        manager_ref.append(manager)
        try:
            if phase == "reuse":
                await lease_client(manager, "room-a")
                armed["on"] = True
            sessions_before = len(claude.sessions)
            with pytest.raises(
                (ClaudeSessionManagerStoppedError, asyncio.CancelledError)
            ):
                async with asyncio.timeout(10):
                    await lease_client(manager, "room-a")

            if phase == "post-status":
                # Built and confirmed, then refused instead of published.
                assert len(claude.sessions) == 1
                assert not claude.sessions[0].alive
            else:
                assert len(claude.sessions) == sessions_before
            if phase == "reservation":
                assert resolutions == []
            assert not manager.has_session("room-a") or phase == "reuse"
        finally:
            await manager.stop()
        assert not bool(manager._rooms)
        assert all(not s.alive for s in claude.sessions)
        assert list(owned_tmp.glob("band-claude-mcp-*")) == []

    @pytest.mark.parametrize("mode", ["materialized", "deferred"])
    async def test_the_reuse_decision_is_a_plain_bool_by_the_final_guard(
        self,
        tmp_path: Path,
        claude: FakeClaude,
        mode: str,
    ) -> None:
        """The reuse decision is reduced to a plain bool once, inside the fallible
        prerequisite boundary, before the callback-free admission guard. An object
        answering the currentness comparison is therefore never asked its truth
        again: a callback that would close admission at the branch test cannot run
        there at all (the deferred case), and closing on the first read is refused
        outright (the control)."""

        def band(room_id: str) -> Any:
            return dict(BAND)

        manager = ClaudeSessionManager(
            ClaudeAgentOptions(),
            mcp_servers_factory=band,
            workspaces=RoomWorkspaces(lambda room_id: str(tmp_path / room_id)),
        )
        claude.mcp_statuses.append(
            [{"name": BAND_MCP_SERVER_NAME, "status": "connected"}]
        )
        client = await lease_client(manager, "room-a")
        custody = ClientCustody(client)
        real_options = client.options
        probe = ReuseProbe(manager, mode)
        sessions_before = len(claude.sessions)
        try:
            client.options = HeldServers(probe)
            if mode == "materialized":  # closes while the decision is materialized
                with pytest.raises(ClaudeSessionManagerStoppedError):
                    await lease_client(manager, "room-a")
            else:  # a second read, which the settled decision must never take
                returned = await lease_client(manager, "room-a")
                assert probe.reads == 1, f"the decision was read {probe.reads} times"
                assert not manager._retention.stopping
                assert returned is client
        finally:
            client.options = real_options
            await manager.stop()
        assert all(not s.alive for s in claude.sessions)
        assert len(claude.sessions) == sessions_before
        assert not bool(manager._rooms)
        custody.assert_disconnected()

    @pytest.mark.parametrize("with_resolver", [True, False])
    async def test_a_failing_reuse_decision_stays_a_safe_non_resume_failure(
        self,
        tmp_path: Path,
        claude: FakeClaude,
        caplog: pytest.LogCaptureFixture,
        with_resolver: bool,
    ) -> None:
        """A truth test that raises while the reuse decision is worked out is a
        reservation failure the owner built, with or without a resolver: its text
        carries no callback value, and the adapter never retries the room without
        its history."""
        secret = "res-ul3t-9a7c"

        def band(room_id: str) -> Any:
            return dict(BAND)

        async def resolver(workspace: str) -> Mapping[str, Any]:
            return {}

        manager = ClaudeSessionManager(
            ClaudeAgentOptions(),
            mcp_servers_factory=band,
            workspaces=RoomWorkspaces(lambda room_id: str(tmp_path / room_id)),
            resolve_mcp_servers=resolver if with_resolver else None,
        )
        calls: list[str | None] = []

        async def counted(
            room_id: str, *, resume_session_id: str | None = None
        ) -> ClaudeSDKClient:
            calls.append(resume_session_id)
            return await lease_client(
                manager, room_id, resume_session_id=resume_session_id
            )

        claude.mcp_statuses.append(
            [{"name": BAND_MCP_SERVER_NAME, "status": "connected"}]
        )
        client = await lease_client(manager, "room-a")
        custody = ClientCustody(client)
        probe = RaisingTruth(secret)
        real_options = client.options
        session = manager.room("room-a")
        real_identity = session._protected_servers
        try:
            if with_resolver:  # the retained protected servers are the comparison
                session._protected_servers = probe
            else:  # what the held client dials is the comparison
                client.options = HeldServers(probe)
            # The decision's truth test runs inside the fallible boundary; the
            # branch test after the callback-free guard reads the settled bool.
            with (
                caplog.at_level(logging.WARNING),
                pytest.raises(ClaudeMcpStartupError) as caught,
            ):
                await counted("room-a", resume_session_id="hist-1")
        finally:
            client.options = real_options
            if real_identity is not None:
                session._protected_servers = real_identity
            await manager.stop()
        assert probe.reads == 1, f"the decision was read {probe.reads} times"
        error = caught.value
        assert error.stage == "reservation" and not error.resume_eligible
        assert secret not in str(error)
        assert secret not in caplog.text
        assert calls == ["hist-1"], (
            "the room's session was started again without history"
        )
        assert all(not s.alive for s in claude.sessions)
        assert not bool(manager._rooms)
        custody.assert_disconnected()
