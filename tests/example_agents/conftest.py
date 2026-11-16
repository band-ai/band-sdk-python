"""Shared peers for the example scripts' shell tests."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from tests.paths import EXAMPLES_ROOT


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
    print(json.dumps({'allowed': False}) + os.environ.get('DEMO_SBX_BANNER', ''))
    sys.exit(1)
if args[:2] == ['policy', 'ls']:
    if os.environ.get('DEMO_POLICY_ERROR'):
        sys.exit(42)
    host = os.environ.get('DEMO_POLICY_RESOURCE')
    print(json.dumps({'rules': [{'scope': 'global', 'resources': [host]}] if host else []}) + os.environ.get('DEMO_SBX_BANNER', ''))
if args[:2] == ['secret', 'ls'] and os.environ.get('DEMO_COLLISION'):
    print('existing-secret')
"""
    )
    shim.chmod(0o755)
    uv = binary / "uv"
    uv.write_text(
        # Only the inline `python -c` parses run; other uv steps are no-ops here.
        f'#!/bin/sh\nif [ "$5" != -c ]; then exit 0; fi\nshift 4\nexec {shlex.quote(sys.executable)} "$@"\n'
    )
    uv.chmod(0o755)
    env = os.environ | {
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "DEMO_EVENTS": str(events),
        "DEMO_ENV_FILE": str(settings),
    }
    return script, env, events
