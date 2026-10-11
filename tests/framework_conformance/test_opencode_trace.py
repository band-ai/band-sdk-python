"""Exercise the shipped native plugin over real HTTP streams on every host OS."""

from __future__ import annotations

import shutil
import subprocess

from tests.paths import REPO_ROOT


def test_native_provider_trace_preserves_streams_and_cancellation() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required to verify the native OpenCode plugin"
    result = subprocess.run(
        [
            node,
            "--test",
            "--test-timeout=15000",
            str(REPO_ROOT / "tests/framework_conformance/opencodetrace.mjs"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
