"""The Docker demo must keep each kit entrypoint attached, including headless runs."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests.paths import EXAMPLES_ROOT


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_headless_demo_attaches_each_agent_to_a_tty(tmp_path: Path) -> None:
    tmux = shutil.which("tmux")
    assert tmux is not None

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    socket = f"band-demo-test-{uuid.uuid4().hex}"
    session = f"band-demo-test-{uuid.uuid4().hex[:8]}"
    events = tmp_path / "sbx-events"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = run_dir / "tmux"

    tmux_shim = bin_dir / "tmux"
    tmux_shim.write_text(
        f'#!/bin/sh\nexec {shlex.quote(tmux)} -L "{socket}" -f /dev/null "$@"\n'
    )
    tmux_shim.chmod(0o755)
    sbx_shim = bin_dir / "sbx"
    sbx_shim.write_text(
        "#!/bin/sh\n"
        '[ "$1" = run ] || exit 0\n'
        "if [ -t 0 ]; then tty=tty; else tty=pipe; fi\n"
        'printf "%s %s %s %s\\n" "$1" "$2" "$3" "$tty" >> "$SBX_EVENTS"\n'
        "while :; do sleep 1; done\n"
    )
    sbx_shim.chmod(0o755)
    uv_shim = bin_dir / "uv"
    uv_shim.write_text("#!/bin/sh\nexit 0\n")
    uv_shim.chmod(0o755)

    env = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SBX_EVENTS": str(events),
        "DEMO_HEADLESS": "1",
        "DEMO_ENV_FILE": str(tmp_path / "missing-env"),
    }
    script = EXAMPLES_ROOT / "docker_demo" / "launch.sh"
    try:
        subprocess.run(
            [
                "bash",
                "-c",
                """source "$1"
MF_TMUX="$2"
TMUX_SESSION="$3"
check_attach_tool
attach_one pm band-demo-pm
attach_one dev band-demo-dev
attach_one architect band-demo-architect""",
                "bash",
                str(script),
                str(manifest),
                session,
            ],
            env=env,
            check=True,
            timeout=15,
        )

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            attached = events.read_text().splitlines() if events.exists() else []
            if len(attached) == 3:
                break
            time.sleep(0.05)

        assert sorted(attached) == [
            "run --name band-demo-architect tty",
            "run --name band-demo-dev tty",
            "run --name band-demo-pm tty",
        ]
        assert manifest.read_text().splitlines() == [session]

        subprocess.run(
            [
                "bash",
                "-c",
                """source "$1"
HERE="$2"
RUN_DIR="$3"
MF_TMUX="$RUN_DIR/tmux"
MF_SANDBOXES="$RUN_DIR/sandboxes"
MF_SECRETS="$RUN_DIR/secrets"
MF_POLICY="$RUN_DIR/policy"
cleanup""",
                "bash",
                str(script),
                str(tmp_path),
                str(run_dir),
            ],
            env=env,
            check=True,
            timeout=15,
        )
        assert not run_dir.exists()
        assert (
            subprocess.run(
                [str(tmux_shim), "has-session", "-t", session],
                env=env,
                capture_output=True,
                check=False,
            ).returncode
            != 0
        )
    finally:
        subprocess.run(
            [str(tmux_shim), "kill-session", "-t", session],
            env=env,
            capture_output=True,
            check=False,
        )
