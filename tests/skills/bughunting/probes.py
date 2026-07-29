"""Subprocess entry points for the runner's real-process tests.

Signal handling and process-group teardown cannot be proven against a mock: a
just-exited child still reports ``returncode is None``, and a runner killed by
SIGTERM leaves orphans no in-process fake can show. These modes are launched as
real processes by ``test_runner.py``.

``sleep`` is the example being torn down; ``termination`` is the runner itself,
so that sending it a real SIGTERM demonstrates whether its cleanup runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

from tests.paths import REPO_ROOT
from tests.skills.bughunting.scripts import loaded_script

# Long enough that nothing exits on its own inside a test, short enough that a
# leaked process cannot outlive the test session by much.
LIFETIME_S = 300.0


def run_sleep(pid_file: Path | None, ignore_sigint: bool, with_peer: bool) -> None:
    """Stay alive until signalled, recording the pids a test must watch die.

    ``ignore_sigint`` makes the escalation observable: SIGINT alone cannot end
    this process, so termination must reach SIGTERM. ``with_peer`` adds a child
    of its own — in the same process group — which only a group-wide signal
    reaches.
    """
    if ignore_sigint:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    pids = [os.getpid()]
    if with_peer:
        command = [sys.executable, __file__, "sleep"]
        if ignore_sigint:
            command.append("--ignore-sigint")
        pids.append(subprocess.Popen(command).pid)
    if pid_file is not None:
        pid_file.write_text("\n".join(str(pid) for pid in pids), encoding="utf-8")
    time.sleep(LIFETIME_S)


def example_spec(runner: ModuleType, pid_file: Path) -> object:
    """A plan spec whose "example" is this file's ``sleep`` mode."""
    return runner.ExampleSpec(
        id="probe",
        path=Path(__file__),
        config_key="agent",
        command=(sys.executable, __file__, "sleep", "--pid-file", str(pid_file)),
    )


async def run_termination(pid_file: Path) -> None:
    """Run one example under the runner's termination handling, then wait."""
    with loaded_script("runner") as runner:
        # The readiness budget's value is not what this probe exercises.
        runner.STARTUP_READINESS_S = 0.3
        settings = SimpleNamespace(
            endpoints=SimpleNamespace(
                rest_url="https://test.invalid", ws_url="wss://test.invalid/socket"
            )
        )
        agent = SimpleNamespace(id="agent-id", api_key="private-key", name="agent")
        with runner.cancel_on_termination():
            running = await runner.start_example(
                example_spec(runner, pid_file), agent, REPO_ROOT, settings
            )
            try:
                print("READY", flush=True)  # startup is over; the run is steady
                await asyncio.Event().wait()
            finally:
                await runner.stop_example(running)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("mode", choices=("sleep", "termination"))
    result.add_argument("--pid-file", type=Path)
    result.add_argument("--ignore-sigint", action="store_true")
    result.add_argument("--with-peer", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    match args.mode:
        case "sleep":
            run_sleep(args.pid_file, args.ignore_sigint, args.with_peer)
        case "termination":
            try:
                asyncio.run(run_termination(args.pid_file))
            except asyncio.CancelledError:
                # Termination handling unwound the run; cleanup already ran.
                print("CLEANED", flush=True)


if __name__ == "__main__":
    main()
