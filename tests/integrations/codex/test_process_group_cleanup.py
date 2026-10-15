"""Real local process groups must settle before Codex workspace custody ends.

Each scenario runs in an isolated Python worker. On Linux that worker is a
subreaper: an orphan zombie still makes killpg(pgid, 0) succeed, and container
PID 1 is not necessarily a reaper. Only the recorded descendant is waitpid'd;
pytest's process-wide child watcher and subreaper state are never changed.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import logging
import os
import signal
import sys
import textwrap
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.integrations.codex import CodexStdioClient
from tests.paths import REPO_ROOT

logger = logging.getLogger(__name__)

POLL_S = 0.02
QUIET_S = 0.2
READINESS_BOUND_S = 5
CLOSE_BOUND_S = 8
CLEANUP_BOUND_S = 4
WORKER_BOUND_S = 20
FAILSAFE_LIFETIME_S = 45
SUBREAPER_UNAVAILABLE_EXIT = 77
PR_SET_CHILD_SUBREAPER = 36

LEADER_READY = "leader-ready"
DESCENDANT_READY = "descendant-ready"
DESCENDANT_PID = "descendant-pid"
LEADER_EOF = "leader-eof"
HEARTBEAT = "heartbeat"
TERM_OBSERVED = "term-observed"
FAILSAFE_EXPIRED = "failsafe-expired"
ROOM_ID = "owned-room"
OTHER_ROOM_ID = "other-room"
NORMAL_CLOSE = "normal-close"
IGNORE_TERM = "ignore-term"
UNKNOWN_EMPTY_PROBE = "unknown-empty-probe"
TRANSIENT_EMPTY_PROBE = "transient-empty-probe"
WORKER_MODULE = "tests.integrations.codex.test_process_group_cleanup"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "fork") or not hasattr(os, "killpg"),
    reason="requires POSIX sessions, fork, and process-group signals",
)

LEADER = textwrap.dedent(
    f"""
    import os
    import signal
    import sys
    import time
    from pathlib import Path

    def publish(name, value):
        temporary = Path(name + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(name)

    def expire(_signal, _frame):
        publish({FAILSAFE_EXPIRED!r}, str(os.getpid()))
        os._exit(99)

    signal.signal(signal.SIGALRM, expire)
    signal.alarm({FAILSAFE_LIFETIME_S})
    descendant = os.fork()
    if descendant == 0:
        # Inherited pipes would keep process.wait() pending after normal EOF,
        # accidentally exercising the leader-timeout path instead.
        null = os.open(os.devnull, os.O_RDWR)
        for descriptor in (0, 1, 2):
            os.dup2(null, descriptor)
        if null > 2:
            os.close(null)
        signal.alarm({FAILSAFE_LIFETIME_S})
        ignore_term = sys.argv[1] == {IGNORE_TERM!r}

        def on_term(_signal, _frame):
            publish({TERM_OBSERVED!r}, "ignored" if ignore_term else "accepted")
            if not ignore_term:
                os._exit(0)

        signal.signal(signal.SIGTERM, on_term)
        publish(
            {DESCENDANT_READY!r},
            f"{{os.getpid()}} {{os.getpgrp()}} {{os.getsid(0)}}",
        )
        while True:
            with open({HEARTBEAT!r}, "a", encoding="utf-8") as heartbeat:
                heartbeat.write(".")
            time.sleep({POLL_S})

    publish({DESCENDANT_PID!r}, str(descendant))
    publish(
        {LEADER_READY!r},
        f"{{os.getpid()}} {{os.getpgrp()}} {{os.getsid(0)}}",
    )
    for _line in sys.stdin:
        pass
    publish({LEADER_EOF!r}, "normal")
    """
)

ProcessGroup = tuple[
    CodexStdioClient,
    asyncio.subprocess.Process,
    asyncio.Task[int | None],
]
ScenarioRunner = Callable[[str], Awaitable[None]]
GroupSignal = Callable[[int, int], None]


def leader_command(*, ignore_term: bool = False) -> tuple[str, ...]:
    mode = IGNORE_TERM if ignore_term else NORMAL_CLOSE
    return (sys.executable, "-u", "-c", LEADER, mode)


def group_is_empty(pgid: int, killpg: GroupSignal) -> bool:
    try:
        killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


async def wait_until(observed: Callable[[], bool]) -> None:
    async with asyncio.timeout(READINESS_BOUND_S):
        while not observed():
            await asyncio.sleep(POLL_S)


def poll_descendant_exit(descendant_pid: int) -> int | None:
    """Poll only the recorded adopted child without blocking the worker loop."""
    try:
        reaped, status = os.waitpid(descendant_pid, os.WNOHANG)
    except ChildProcessError:
        return None
    return status if reaped else None


async def reap_descendant(
    descendant_pid: int,
    pgid: int,
    killpg: GroupSignal,
) -> int | None:
    if sys.platform != "linux":
        while not group_is_empty(pgid, killpg):
            await asyncio.sleep(POLL_S)
        return None
    while True:
        status = poll_descendant_exit(descendant_pid)
        if status is not None:
            return status
        await asyncio.sleep(POLL_S)


async def clean_owned_group(
    client: CodexStdioClient,
    process: asyncio.subprocess.Process,
    descendant: asyncio.Task[int | None] | None,
    killpg: GroupSignal,
) -> None:
    # A live recorded descendant pins this original group identity. Once both
    # recorded processes are reaped, never signal a potentially recycled PGID.
    descendant_settled = (
        descendant is not None
        and descendant.done()
        and not descendant.cancelled()
        and descendant.exception() is None
    )
    still_owned = process.returncode is None or not descendant_settled
    if still_owned and not group_is_empty(process.pid, killpg):
        with suppress(ProcessLookupError):
            killpg(process.pid, signal.SIGKILL)
    await process.wait()
    if descendant is not None:
        await descendant
    assert group_is_empty(process.pid, killpg), "fixture's original group did not empty"
    await client.close()


@asynccontextmanager
async def owned_group(
    workspace: Path,
    *,
    ignore_term: bool = False,
    client: CodexStdioClient | None = None,
) -> AsyncIterator[ProcessGroup]:
    client = client or CodexStdioClient(
        command=leader_command(ignore_term=ignore_term), cwd=str(workspace)
    )
    native_killpg = os.killpg
    descendant: asyncio.Task[int | None] | None = None
    await client.connect()
    process = client._proc
    assert process is not None
    try:
        await wait_until(
            lambda: (
                (workspace / LEADER_READY).exists()
                and (workspace / DESCENDANT_READY).exists()
                and (workspace / HEARTBEAT).exists()
            )
        )
        leader_identity = tuple(
            int(value) for value in (workspace / LEADER_READY).read_text().split()
        )
        descendant_pid, descendant_group, descendant_session = (
            int(value) for value in (workspace / DESCENDANT_READY).read_text().split()
        )
        recorded_pid = int((workspace / DESCENDANT_PID).read_text())
        descendant = asyncio.create_task(
            reap_descendant(recorded_pid, process.pid, native_killpg)
        )
        assert leader_identity == (process.pid, process.pid, process.pid)
        assert descendant_pid == recorded_pid
        assert descendant_pid != process.pid
        assert (descendant_group, descendant_session) == (process.pid, process.pid)
        initial_heartbeat = (workspace / HEARTBEAT).stat().st_size
        await wait_until(
            lambda: (workspace / HEARTBEAT).stat().st_size > initial_heartbeat
        )
        assert process.returncode is None
        assert not group_is_empty(process.pid, native_killpg)
        yield client, process, descendant
    finally:
        primary_error = sys.exception()
        try:
            if descendant is None and (workspace / DESCENDANT_PID).exists():
                recorded_pid = int((workspace / DESCENDANT_PID).read_text())
                descendant = asyncio.create_task(
                    reap_descendant(recorded_pid, process.pid, native_killpg)
                )
            async with asyncio.timeout(CLEANUP_BOUND_S):
                await clean_owned_group(client, process, descendant, native_killpg)
        except BaseException:
            # Teardown must not turn the behavioral regression into a different
            # cleanup exception. With no primary failure, cleanup still fails loudly.
            if primary_error is None:
                raise
            logger.exception("Local process-group fixture cleanup failed")


async def assert_settled(
    workspace: Path,
    group: ProcessGroup,
    killpg: GroupSignal,
) -> int | None:
    _client, process, descendant = group
    assert process.returncode == 0, "leader did not exit normally on stdin EOF"
    assert (workspace / LEADER_EOF).read_text() == "normal"
    assert group_is_empty(process.pid, killpg), (
        "normal leader exit left its originally owned group alive"
    )
    status = await asyncio.wait_for(asyncio.shield(descendant), CLEANUP_BOUND_S)
    assert (workspace / TERM_OBSERVED).exists(), "owned descendant never received TERM"
    assert not (workspace / FAILSAFE_EXPIRED).exists(), (
        "failsafe, not close(), stopped work"
    )
    stopped_at = (workspace / HEARTBEAT).stat().st_size
    # Quiescence is itself the observation: span multiple scheduled heartbeats.
    await asyncio.sleep(QUIET_S)
    assert (workspace / HEARTBEAT).stat().st_size == stopped_at
    return status


async def normal_close_scenario(workspace: Path) -> None:
    async with owned_group(workspace) as group:
        client, _process, _descendant = group
        await asyncio.wait_for(client.close(), CLOSE_BOUND_S)
        status = await assert_settled(workspace, group, os.killpg)
        assert (workspace / TERM_OBSERVED).read_text() == "accepted"
        if status is not None:
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


async def ignoring_term_scenario(workspace: Path) -> None:
    async with owned_group(workspace, ignore_term=True) as group:
        client, _process, _descendant = group
        await asyncio.wait_for(client.close(), CLOSE_BOUND_S)
        status = await assert_settled(workspace, group, os.killpg)
        assert (workspace / TERM_OBSERVED).read_text() == "ignored"
        if status is not None:
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL


async def transient_empty_probe_scenario(workspace: Path) -> None:
    native_killpg = os.killpg
    async with owned_group(workspace) as group:
        client, process, _descendant = group
        uncertain_probes = []

        def transient_probe(pgid: int, signum: int) -> None:
            try:
                native_killpg(pgid, signum)
            except ProcessLookupError:
                if pgid == process.pid and signum == 0 and not uncertain_probes:
                    uncertain_probes.append(pgid)
                    raise PermissionError(
                        errno.EPERM, "transient empty-group observation"
                    )
                raise

        with patch.object(os, "killpg", transient_probe):
            await asyncio.wait_for(client.close(), CLOSE_BOUND_S)
        assert uncertain_probes, "the transient unknown observation was not exercised"
        await assert_settled(workspace, group, native_killpg)


async def unknown_empty_probe_scenario(workspace: Path) -> None:
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            codex_command=leader_command(),
            workspace_for_room=lambda _room_id: str(workspace),
            client_close_timeout_s=CLOSE_BOUND_S,
        )
    )
    room = adapter._room_client(ROOM_ID)
    adapter._active_room.set(ROOM_ID)
    client = adapter._build_client(room, None)
    room.client = client

    async def acquire(_generation: int, _resume: bool) -> None:
        await client.connect()

    room.retained._acquire = acquire
    async with room.retained.use():
        pass
    native_killpg = os.killpg
    uncertain_probes: list[int] = []
    async with owned_group(workspace, client=client) as group:
        _client, process, _descendant = group
        original_pgid = process.pid

        def uncertain_empty_probe(pgid: int, signum: int) -> None:
            try:
                native_killpg(pgid, signum)
            except ProcessLookupError:
                if pgid != original_pgid or signum != 0:
                    raise
                # Only the observation of a genuinely empty owned group fails.
                # TERM/KILL delivery and observations of live groups stay real.
                uncertain_probes.append(pgid)
                raise PermissionError(
                    errno.EPERM, "group-empty observation unavailable"
                )

        try:
            with patch.object(os, "killpg", uncertain_empty_probe):
                with pytest.raises(CleanupIncompleteError):
                    await asyncio.wait_for(adapter.on_cleanup(ROOM_ID), CLOSE_BOUND_S)
                assert uncertain_probes, (
                    "cleanup never tried to confirm its empty group"
                )
                assert adapter._room_clients[ROOM_ID] is room
                assert room.client is client
                assert adapter._workspaces.workspace(ROOM_ID) == str(workspace)
                assert room.retained.closing
                with pytest.raises(ValueError, match="assigned .* to both"):
                    adapter._workspaces.claim(OTHER_ROOM_ID, room.workspace)
                await assert_settled(workspace, group, native_killpg)

            await asyncio.wait_for(adapter.on_cleanup(ROOM_ID), CLOSE_BOUND_S)
            assert room.client is None
            assert ROOM_ID not in adapter._room_clients
            assert ROOM_ID not in adapter._workspaces.rooms
            assert (
                adapter._workspaces.claim(OTHER_ROOM_ID, room.workspace)
                == room.workspace
            )
            await assert_settled(workspace, group, native_killpg)
        finally:
            primary_error = sys.exception()
            try:
                await asyncio.wait_for(adapter.on_cleanup(ROOM_ID), CLOSE_BOUND_S)
            except BaseException:
                if primary_error is None:
                    raise
                logger.exception("Local adapter cleanup failed")
            finally:
                adapter._workspaces.release(OTHER_ROOM_ID)


def enable_worker_subreaper() -> None:
    if sys.platform != "linux":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = getattr(libc, "prctl", None)
    if prctl is None:
        logger.error("Linux test worker has no prctl child-subreaper support")
        raise SystemExit(SUBREAPER_UNAVAILABLE_EXIT)
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        logger.error(
            "Linux test worker cannot enable child subreaping: %s", os.strerror(error)
        )
        raise SystemExit(SUBREAPER_UNAVAILABLE_EXIT)


async def run_worker(scenario: str, workspace: Path) -> None:
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        scenarios = {
            NORMAL_CLOSE: normal_close_scenario,
            IGNORE_TERM: ignoring_term_scenario,
            UNKNOWN_EMPTY_PROBE: unknown_empty_probe_scenario,
            TRANSIENT_EMPTY_PROBE: transient_empty_probe_scenario,
        }
        try:
            exercise = scenarios[scenario]
        except KeyError as error:
            raise ValueError(f"unknown process-group scenario: {scenario}") from error
        await exercise(workspace)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


@pytest_asyncio.fixture(loop_scope="function")
async def scenario_runner(tmp_path: Path) -> AsyncIterator[ScenarioRunner]:
    workers: list[asyncio.subprocess.Process] = []

    async def run(scenario: str) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            WORKER_MODULE,
            scenario,
            str(tmp_path),
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        workers.append(process)
        stdout, stderr = await asyncio.wait_for(process.communicate(), WORKER_BOUND_S)
        diagnostic = (stdout + stderr).decode("utf-8", errors="replace")
        if process.returncode == SUBREAPER_UNAVAILABLE_EXIT:
            pytest.skip(diagnostic)
        assert process.returncode == 0, diagnostic

    try:
        yield run
    finally:
        for process in workers:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), CLEANUP_BOUND_S + 1)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await asyncio.wait_for(process.wait(), CLEANUP_BOUND_S)


class TestProcessGroupCleanup:
    async def test_normal_close_settles_descendant_after_leader_stdin_eof(
        self, scenario_runner: ScenarioRunner
    ) -> None:
        await scenario_runner(NORMAL_CLOSE)

    async def test_term_ignoring_descendant_is_killed_after_normal_leader_exit(
        self, scenario_runner: ScenarioRunner
    ) -> None:
        await scenario_runner(IGNORE_TERM)

    async def test_unknown_group_empty_probe_retains_real_adapter_client_and_claim_until_retry(
        self, scenario_runner: ScenarioRunner
    ) -> None:
        await scenario_runner(UNKNOWN_EMPTY_PROBE)

    async def test_transient_unknown_empty_probe_is_confirmed_within_close_bound(
        self, scenario_runner: ScenarioRunner
    ) -> None:
        await scenario_runner(TRANSIENT_EMPTY_PROBE)


if __name__ == "__main__":
    enable_worker_subreaper()
    asyncio.run(run_worker(sys.argv[1], Path(sys.argv[2])))
