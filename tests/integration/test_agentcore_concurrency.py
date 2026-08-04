"""Integration test: concurrent AgentCore container instances on one host.

Verifies INT-527's L3 "co-resident instances" requirement for the AgentCore
container: N instances must start independently with no manual per-instance
configuration beyond a port, and no port, lock-file, or shared-resource
collision.

Each instance's startup() makes a real Band REST call (fetching agent
identity), so this needs BAND_API_KEY/TEST_AGENT_ID from .env.test.
ANTHROPIC_API_KEY is a dummy — no turn is ever driven through /invocations,
so proving this property needs no real LLM key.

Run with: uv run pytest tests/integration/test_agentcore_concurrency.py -v -s
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from tests.conftest_integration import get_api_key, get_base_url, get_test_agent_id
from tests.integration.conftest import requires_api
from tests.paths import EXAMPLES_ROOT

_SCRIPT = EXAMPLES_ROOT / "agentcore" / "custom_tools_llm_server.py"
_STARTUP_TIMEOUT = 15.0
_POLL_INTERVAL = 0.5


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn(port: int) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "BAND_AGENT_ID": get_test_agent_id() or "",
        "BAND_API_KEY": get_api_key() or "",
        "ANTHROPIC_API_KEY": "test-anthropic",
        "BAND_REST_URL": get_base_url(),
        "PORT": str(port),
        "LOG_LEVEL": "WARNING",
    }
    return subprocess.Popen(
        [sys.executable, str(_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _wait_for_ping(port: int, deadline: float) -> str:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/ping", timeout=2
            ) as resp:
                return resp.read().decode()
        except (urllib.error.URLError, ConnectionError) as e:
            last_error = e
            time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"port {port} never answered /ping: {last_error}")


@requires_api
class TestConcurrentContainerInstances:
    """L3 co-resident instances: no port, lock-file, or shared-resource collision."""

    def test_three_instances_start_independently_on_one_host(self) -> None:
        ports = [_free_port(), _free_port(), _free_port()]
        procs = [_spawn(port) for port in ports]
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        try:
            for port, proc in zip(ports, procs):
                if proc.poll() is not None:
                    out = proc.stdout.read() if proc.stdout else ""
                    pytest.fail(f"port {port} exited early ({proc.returncode}):\n{out}")
                assert "Healthy" in _wait_for_ping(port, deadline)
        finally:
            for proc in procs:
                _terminate(proc)

    def test_reusing_a_bound_port_fails_at_the_socket_not_the_app(self) -> None:
        """The only per-instance resource is the port — nothing else
        silently collides. A deliberate port clash fails at the OS bind
        step, not because of a shared lock file or app-level singleton.
        """
        port = _free_port()
        holder = _spawn(port)
        try:
            _wait_for_ping(port, time.monotonic() + _STARTUP_TIMEOUT)

            clasher = _spawn(port)
            try:
                # communicate() drains stdout while waiting — a plain wait()
                # risks deadlock if the child blocks on a full pipe buffer.
                out, _ = clasher.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                clasher.kill()
                out, _ = clasher.communicate()
            returncode = clasher.returncode

            assert returncode != 0
            # asyncio's own bind-error wrapper text (stdlib, same across
            # platforms) — the OSError strerror after it is OS-specific
            # ("address already in use" vs. Windows' own wording).
            assert "attempting to bind" in out
        finally:
            _terminate(holder)
