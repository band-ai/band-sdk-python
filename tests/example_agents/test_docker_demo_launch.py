"""The Docker demo must keep each kit entrypoint attached, including headless runs."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
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


@pytest.fixture
def demo_shell(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    if os.name != "posix":
        pytest.skip("The demo uses a POSIX shell and executable script peers")
    repo = tmp_path / "repo"
    demo = repo / "examples" / "docker_demo"
    demo.mkdir(parents=True)
    script = demo / "launch.sh"
    shutil.copyfile(EXAMPLES_ROOT / "docker_demo" / "launch.sh", script)
    (repo / ".env.test").write_text(
        "BAND_API_KEY_USER=canonical-user\nBAND_REST_URL=https://canonical.band.test\nBAND_WS_URL=wss://canonical.band.test/socket\n"
    )
    settings = demo / "settings.env"
    settings.write_text(
        "BAND_API_KEY_USER=wrong-user\nBAND_REST_URL=https://wrong.test\nOPENAI_API_KEY=demo-provider\n"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    events = tmp_path / "events.jsonl"
    shim = binary / "sbx"
    shim.write_text(
        f"#!{sys.executable}\n"
        + r"""import json, os, sys
args = sys.argv[1:]
value = sys.stdin.read() if args[:2] == ['secret', 'set-custom'] else None
with open(os.environ['DEMO_EVENTS'], 'a') as events:
    events.write(json.dumps({'args': args, 'input': value}) + '\n')
if args[0] == 'ls' and os.environ.get('DEMO_DISCOVERY_ERROR'):
    sys.exit(42)
if args[0] == os.environ.get('DEMO_MUTATION_FAIL') or '-'.join(args[:2]) == os.environ.get('DEMO_MUTATION_FAIL'):
    sys.exit(42)
if args[:2] == ['policy', 'check']:
    if os.environ.get('DEMO_POLICY_ERROR'):
        sys.exit(42)
    print(json.dumps({'allowed': False}))
    sys.exit(1)
if args[:2] == ['policy', 'ls']:
    if os.environ.get('DEMO_POLICY_ERROR'):
        sys.exit(42)
    host = os.environ.get('DEMO_POLICY_RESOURCE')
    print(json.dumps({'rules': [{'scope': 'global', 'resources': [host]}] if host else []}))
if args[:2] == ['secret', 'ls'] and os.environ.get('DEMO_COLLISION'):
    print('existing-secret')
"""
    )
    shim.chmod(0o755)
    uv = binary / "uv"
    uv.write_text(
        f'#!/bin/sh\nif [ "$2" != --no-project ]; then exit 0; fi\nshift 3\nexec {shlex.quote(sys.executable)} "$@"\n'
    )
    uv.chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "DEMO_EVENTS": str(events),
        "DEMO_ENV_FILE": str(settings),
    }
    return script, env, events


def shell(
    script: Path, env: dict[str, str], command: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"\n' + command, "bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_demo_band_identity_always_comes_from_repo_env(
    demo_shell: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _ = demo_shell
    result = shell(
        script,
        env,
        'printf "%s %s %s" "$BAND_API_KEY_USER" "$BAND_REST_URL" "$OPENAI_API_KEY"',
    )
    assert result.returncode == 0
    assert result.stdout == "canonical-user https://canonical.band.test demo-provider"


def test_demo_refuses_unowned_scoped_secret_before_provision(
    demo_shell: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, events = demo_shell
    result = shell(
        script, env | {"DEMO_COLLISION": "1"}, "assert_names_free\nprovision"
    )
    assert result.returncode != 0
    assert "unowned scoped secrets" in result.stderr
    calls = [json.loads(line)["args"] for line in events.read_text().splitlines()]
    assert all(call[:2] != ["secret", "rm"] for call in calls)
    assert all(call[:2] != ["secret", "set-custom"] for call in calls)


def test_demo_sends_secret_stdin_before_positional_workload_create(
    demo_shell: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, events = demo_shell
    workspace = script.parent / "agents" / "dev"
    workspace.mkdir(parents=True)
    (workspace / "band.yaml").write_text(
        "agent:\n  id: placeholder\n  restUrl: https://example.test\n  wsUrl: wss://example.test/socket\n"
    )
    result = shell(
        script,
        env,
        """mkdir -p "$RUN_DIR"
DEMO_DEV_ID=agent-123
DEMO_DEV_APIKEY=agent-nonsecret-fixture
attach_one() { :; }
launch_one dev agents/dev band-demo-dev band-python-kit-cli:local openai""",
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in events.read_text().splitlines()]
    assert [call["args"][0] for call in calls] == ["secret", "secret", "create"]
    assert [call["input"] for call in calls[:2]] == [
        "demo-provider",
        "agent-nonsecret-fixture",
    ]
    assert calls[2]["args"][:4] == [
        "create",
        "--name",
        "band-demo-dev",
        "band-python-kit-cli:local",
    ]
    assert all("--value" not in call["args"] for call in calls)


def test_demo_discovery_failure_blocks_provision(
    demo_shell: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, events = demo_shell
    result = shell(
        script, env | {"DEMO_DISCOVERY_ERROR": "1"}, "assert_names_free\nprovision"
    )
    assert result.returncode != 0
    assert "could not inspect existing sandboxes" in result.stderr
    assert [
        json.loads(line)["args"][0] for line in events.read_text().splitlines()
    ] == ["ls"]


@pytest.mark.parametrize(
    "mutation,ledger,resource",
    [
        ("secret-set-custom", "secrets", "sk-band-demo"),
        ("create", "sandboxes", "band-demo-dev"),
    ],
)
def test_demo_persists_cleanup_intent_before_failed_mutation(
    demo_shell: tuple[Path, dict[str, str], Path],
    mutation: str,
    ledger: str,
    resource: str,
) -> None:
    script, env, _ = demo_shell
    workspace = script.parent / "agents" / "dev"
    workspace.mkdir(parents=True)
    (workspace / "band.yaml").write_text(
        "agent:\n  id: placeholder\n  restUrl: https://example.test\n  wsUrl: wss://example.test/socket\n"
    )
    result = shell(
        script,
        env | {"DEMO_MUTATION_FAIL": mutation},
        """mkdir -p "$RUN_DIR"
assert_names_free
DEMO_DEV_ID=agent-123
DEMO_DEV_APIKEY=agent-nonsecret-fixture
attach_one() { :; }
launch_one dev agents/dev band-demo-dev band-python-kit-cli:local openai""",
    )
    assert result.returncode != 0
    assert resource in (script.parent / ".demo" / "run" / ledger).read_text()


def test_demo_persists_policy_intent_before_failed_allow(
    demo_shell: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, _ = demo_shell
    # Check is a confirmed denial; the allow operation fails after its request.
    result = shell(
        script,
        env | {"DEMO_MUTATION_FAIL": "policy-allow"},
        'mkdir -p "$RUN_DIR"\nallow_global demo.band.test',
    )
    assert result.returncode != 0
    assert (script.parent / ".demo" / "run" / "policy").read_text().splitlines() == [
        "demo.band.test"
    ]


@pytest.mark.parametrize("failure", [False, True])
def test_demo_policy_cleanup_requires_confirmed_absence(
    demo_shell: tuple[Path, dict[str, str], Path], failure: bool
) -> None:
    script, env, _ = demo_shell
    run = script.parent / ".demo" / "run"
    run.mkdir(parents=True)
    (run / "policy").write_text("demo.band.test\n")
    result = shell(
        script, env | ({"DEMO_POLICY_ERROR": "1"} if failure else {}), "cleanup"
    )
    assert (result.returncode != 0) is failure
    assert run.exists() is failure
