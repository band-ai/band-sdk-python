"""Thin `sbx`-CLI seam for driving a Docker Sandbox from the host.

``examples/sandbox/self-registration/demo.py`` uses it to wrap the kit image as
a local workload, network-allow the Band deployment, hold the sandbox's
foreground session on a real pty, and remove the scoped secret. The module
imports cleanly (stdlib + yaml), so its pure helpers are unit-tested in CI:
the kit's network baseline and scoped secret removal in
``tests/docker/test_nevervm_contracts.py``, and the pty-attached process
lifecycle in ``tests/docker/test_sbx_cli.py``.
"""

from __future__ import annotations

import logging
import os
import selectors
import shlex
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from band.docker.sbx_process import sbx_json
from tests.paths import KIT_DIR

logger = logging.getLogger(__name__)

SBX = "sbx"
# How long _stop_pty_process waits for a process to exit after each signal:
# once after `terminate()` before escalating to `kill()`, and again after
# `kill()` before giving up and logging it as still alive.
ATTACH_STOP_TIMEOUT_S = 10
ATTACH_READ_POLL_S = 0.1


def _kit_spec(kit: Path | str) -> dict[str, Any]:
    """The kit's parsed ``spec.yaml``."""
    return yaml.safe_load((Path(kit) / "spec.yaml").read_text(encoding="utf-8"))


def kit_baseline_hosts(kit: Path | str) -> frozenset[str]:
    """The descriptor's runtime network policy, shared by every workload build."""
    capabilities = _kit_spec(kit)["capabilities"]
    network = next(
        entry
        for entry in capabilities
        if entry["type"] == "com.docker.sandbox/network-policy@1"
    )
    return frozenset(network["config"]["runtime"]["allow"])


@contextmanager
def local_workload(image: str) -> Iterator[Path]:
    """Wrap a loaded workload in the canonical declarations for local sbx use.

    sbx builds local descriptors with their directory as context; the SDK
    recipe needs repository-root inputs and is built with Buildx first. sbx
    names the workload after that directory and rejects an ``_`` in it, which
    ``tempfile``'s random suffix often contains, so the name is hex-only.
    """
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary) / f"band-workload-{uuid.uuid4().hex[:12]}"
        yield write_local_workload(image, directory)


def write_local_workload(image: str, directory: Path) -> Path:
    """Wrap a loaded image with the canonical workload declarations."""
    descriptor = _kit_spec(KIT_DIR)
    descriptor.pop("dockerfile")
    descriptor["build"] = f"FROM {image}\n"
    syntax = (KIT_DIR / "spec.yaml").read_text().splitlines()[0]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "band-workload.yaml").write_text(
        syntax + "\n" + yaml.safe_dump(descriptor, sort_keys=False),
        encoding="utf-8",
    )
    return directory


def remove_custom_secret_command(
    *, sandbox: str, placeholder: str = PROXY_MANAGED_API_KEY
) -> list[str]:
    """`sbx secret rm` argv for the scoped secret; ``-f`` skips the prompt."""
    return [
        SBX,
        "secret",
        "rm",
        "--placeholder",
        placeholder,
        "--sandbox",
        sandbox,
        "-f",
    ]


def _network_allowed(host: str) -> bool:
    """Whether the global policy already permits ``host`` (read-only check)."""
    probe = subprocess.run(
        [SBX, "policy", "check", "network", host, "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    allowed = sbx_json(probe.stdout)["allowed"]
    if not isinstance(allowed, bool) or probe.returncode != (0 if allowed else 1):
        raise RuntimeError(f"could not inspect network policy for {host}")
    return allowed


@contextmanager
def allow_network(host: str) -> Iterator[None]:
    """Permit sandbox network access to ``host`` for the block, then revoke it.

    A *global* rule: sbx refuses a per-sandbox grant before the sandbox exists,
    and the agent connects at startup, so the rule must be in force before
    ``sbx create``. Idempotent — when ``host`` is already allowed (the operator
    granted it themselves), this is a no-op and leaves their rule untouched.
    """
    if _network_allowed(host):
        yield
        return
    subprocess.run(
        [SBX, "policy", "allow", "network", host],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    try:
        yield
    finally:
        removal = subprocess.run(
            [SBX, "policy", "rm", "network", "--resource", host],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if removal.returncode != 0:
            raise RuntimeError(
                f"network cleanup incomplete: sbx policy rm network --resource {shlex.quote(host)}"
            )


@contextmanager
def allow_network_for_hosts(hosts: Iterable[str], *, kit: Path | str) -> Iterator[None]:
    """Grant sbx network access, for the block, to any of ``hosts`` not already
    covered by ``kit``'s baseline allowlist (e.g. a non-prod Band deployment).

    Factors out the "for host not in baseline: allow_network(host)" loop that
    every caller pointing a sandbox at a non-prod deployment needs — takes
    plain hostnames rather than a settings/endpoints object, so this module
    stays free of a dependency on the E2E baseline toolkit.
    """
    baseline = kit_baseline_hosts(kit)
    with ExitStack() as stack:
        for host in hosts:
            if host not in baseline:
                stack.enter_context(allow_network(host))
        yield


def _spawn_pty_process(argv: list[str]) -> tuple[subprocess.Popen[bytes], int]:
    """Start `argv` attached to a real pty (its stdin/stdout/stderr), returning
    the child and the controlling end of the pty.

    A backgrounded process without a real pty makes `sbx run` fail after ~30s
    with `inspect exec: context deadline exceeded`, so `attached_run` (below)
    holds one open via the stdlib `pty` module rather than redirecting to a
    pipe or `/dev/null`. `pty` is POSIX-only (no `termios` on Windows) and
    imported here rather than at module level, purely so this module still
    imports cleanly for collection on Windows CI — every actual caller of
    this function only ever runs on POSIX (the self-registration demo
    documents its own prerequisites; the unit tests are Windows-skipped via
    `markers.requires_posix_pty`).
    """
    import pty  # noqa: PLC0415 -- POSIX-only, deferred so this module still collects on Windows

    controller_fd, child_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            argv, stdin=child_fd, stdout=child_fd, stderr=child_fd
        )
    except Exception:
        os.close(controller_fd)
        os.close(child_fd)
        raise
    os.close(child_fd)
    return process, controller_fd


def _stop_pty_process(process: subprocess.Popen[bytes], controller_fd: int) -> None:
    """Terminate `process`, escalating to SIGKILL if it ignores SIGTERM, then
    close `controller_fd` regardless of how it exited."""

    def exited_within_timeout() -> bool:
        try:
            process.wait(timeout=ATTACH_STOP_TIMEOUT_S)
            return True
        except subprocess.TimeoutExpired:
            return False

    try:
        if process.poll() is None:
            process.terminate()
            if not exited_within_timeout():
                logger.warning(
                    "process pid=%s (%s) ignored SIGTERM; sending SIGKILL",
                    process.pid,
                    process.args,
                )
                process.kill()
                if not exited_within_timeout():
                    logger.warning(
                        "process pid=%s (%s) still alive after SIGKILL",
                        process.pid,
                        process.args,
                    )
    finally:
        os.close(controller_fd)


@contextmanager
def attached_run(name: str) -> Iterator[None]:
    """Hold `sbx run --name <name>` attached to a real pty for the block.

    The kit's agent runs on `image entrypoint`, which only launches once
    something attaches — `sbx create` alone leaves the sandbox idle, and `sbx`
    v0.43.0 auto-stops an idle, unattached sandbox within ~60-90s regardless of
    what's running inside it, so a caller that drives `sbx create`/`band-kit
    provision` (e.g. a demo script) holds this open.
    """
    process, controller_fd = _spawn_pty_process([SBX, "run", "--name", name])
    with ExitStack() as ownership:
        ownership.callback(_stop_pty_process, process, controller_fd)
        selector = ownership.enter_context(selectors.DefaultSelector())
        selector.register(controller_fd, selectors.EVENT_READ)
        stopping = threading.Event()

        def drain_output() -> None:
            while not stopping.is_set():
                try:
                    readable = selector.select(ATTACH_READ_POLL_S)
                    if stopping.is_set():
                        return
                    if not readable:
                        continue
                    output = os.read(controller_fd, 65536)
                except OSError:
                    return
                if not output:
                    return
                logger.debug(
                    "sandbox %s: %s", name, output.decode(errors="replace").rstrip()
                )

        reader = threading.Thread(target=drain_output, daemon=True)
        reader.start()
        ownership.callback(reader.join)
        ownership.callback(stopping.set)
        yield
        if process.poll() is not None:
            raise RuntimeError(f"owned sbx run for {name} exited {process.returncode}")


NAME_PREFIX = "band-nevervm"


def sandbox_name(prefix: str = NAME_PREFIX) -> str:
    """A unique sandbox name, allocated before the sandbox exists."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
