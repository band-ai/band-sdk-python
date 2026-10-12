"""Thin `sbx`-CLI seam for the never-in-VM sandbox E2E.

Mirrors :class:`tests.docker.toolkit.docker_cli.Container`, but drives the
**sandbox** flow — `sbx create` / `sbx exec` / `sbx rm` — so requests ride
the host-side proxy that injects the real credentials. That is what a bare
`docker run` cannot exercise, and what the never-in-VM proof needs.

The sandbox-driving methods need a Docker-Sandbox-capable host with the `sbx`
CLI, and run only under the ``sandbox``-marked proof
(``SANDBOX_TESTS_ENABLED=true``). The module itself imports cleanly (stdlib +
yaml), so its pure helpers are unit-tested in CI: scoped secret commands and
the absence-search command in ``tests/docker/test_nevervm_contracts.py``, and
the proxy cert probe and the pty-attached process lifecycle in
``tests/docker/test_sbx_cli.py``.
"""

from __future__ import annotations

import json
import logging
import os
import selectors
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from band.docker.sbx_process import run_sbx_subprocess
from tests.paths import KIT_DIR

logger = logging.getLogger(__name__)

SBX = "sbx"
# `sbx create` provisions a microVM (image pull + boot); generous like the
# docker image build ceiling in docker_cli.
CREATE_TIMEOUT_S = 600
EXEC_TIMEOUT_S = 120
# How long _stop_pty_process waits for a process to exit after each signal:
# once after `terminate()` before escalating to `kill()`, and again after
# `kill()` before giving up and logging it as still alive.
ATTACH_STOP_TIMEOUT_S = 10
ATTACH_READ_POLL_S = 0.1

# The issuer a credential-injected (MITM) TLS connection presents inside a
# sandbox — the never-in-VM proof asserts the Band host's cert shows this,
# confirming the request rode the injection path rather than a plain tunnel.
# Verified live: the injected cert reads `subject: O=Docker Sandboxes; CN=<host>`,
# `issuer: ... Docker Sandboxes Proxy CA`, so this substring matches either line.
PROXY_CA_MARKER = "Docker Sandboxes"

# Read the TLS peer cert the sandbox sees for a host *through* HTTPS_PROXY, so an
# injected (MITM) connection reveals the Docker Sandboxes CA in its issuer. Uses
# only the stdlib (the kit image has Python). Host is argv[1].
_CERT_PROBE = r"""
import os, ssl, socket, sys
from urllib.parse import urlsplit
host = sys.argv[1]
proxy = urlsplit(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
try:
    sock = socket.create_connection((proxy.hostname, proxy.port), timeout=15)
    sock.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s\r\n\r\n" % (host, host)).encode())
    resp = b""
    while b"\r\n\r\n" not in resp:  # full CONNECT reply before TLS; TCP may split it
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("proxy closed before completing CONNECT response")
        resp += chunk
    status = resp.split(b"\r\n", 1)[0].split()
    if len(status) < 2 or not status[1].isdigit() or not 200 <= int(status[1]) < 300:
        raise RuntimeError("proxy CONNECT refused: %r" % resp.split(b"\r\n", 1)[0])
    cafile = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    cert = ctx.wrap_socket(sock, server_hostname=host).getpeercert()
    print("subject:", cert.get("subject"))
    print("issuer:", cert.get("issuer"))
except Exception as exc:
    print("cert-probe-error:", type(exc).__name__, exc)
"""


def sbx_available() -> bool:
    """True when the `sbx` CLI is on PATH (else the sandbox test skips)."""
    return shutil.which(SBX) is not None


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
    recipe needs repository-root inputs and is built with Buildx first.
    """
    with tempfile.TemporaryDirectory(prefix="band-workload-") as temporary:
        yield write_local_workload(image, Path(temporary))


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


def files_containing_command(secret: str, search_paths: Sequence[str]) -> str:
    """Shell command listing files under ``search_paths`` that hold ``secret`` literally.

    The flags are chosen for an absence proof, where a *missed* match is a silent
    hole rather than mere noise:

    - ``-a`` searches every file as text, so a key sitting in an otherwise-binary
      file (a cache/DB with NUL bytes) is still found. grep's default binary
      heuristic can skip such a file — verified: merely dropping ``-I`` still
      missed it under one grep implementation, whereas ``-a`` matched everywhere.
    - ``-F`` matches the secret literally, so a key with ``.``/``$``/``*`` cannot
      over- or under-match.
    - ``-l`` prints only filenames, so the secret itself is never echoed.
    """
    paths = " ".join(shlex.quote(path) for path in search_paths)
    return f"grep -ralF -- {shlex.quote(secret)} {paths} 2>/dev/null || true"


def set_custom_secret_command(
    *, sandbox: str, host: str, env: str, placeholder: str
) -> list[str]:
    """`sbx secret set-custom` argv, scoped to one sandbox (never global ``-g``)."""
    return [SBX, "secret", "set-custom", "--sandbox", sandbox, "--host", host,
            "--env", env, "--placeholder", placeholder]  # fmt: skip


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


def run_redacting_secret(argv: list[str], *, secret: str, timeout: int = 60) -> str:
    """Run a secret-bearing command, redacting ``secret`` from any error.

    A routine failure (daemon down, auth, a dead sandbox) would otherwise spill
    the credential into the raised error and captured test logs — whether the
    secret is a whole argument (``--value <secret>``) or embedded inside one (a
    grep command quoting it). ``run_sbx_subprocess`` does the run-and-redact;
    this just adapts its stdout-on-success/CompletedProcess shape to the
    stdout-only return every caller here wants.
    """
    return run_sbx_subprocess(argv, timeout=timeout, redact=secret).stdout


@contextmanager
def custom_secret(
    *, sandbox: str, host: str, env: str, value: str, placeholder: str
) -> Iterator[None]:
    """Store a scoped secret on stdin before create, then independently remove it."""
    run_sbx_subprocess(
        set_custom_secret_command(
            sandbox=sandbox, host=host, env=env, placeholder=placeholder
        ),
        input=value,
        redact=value,
        timeout=60,
    )
    try:
        yield
    finally:
        removal = subprocess.run(
            remove_custom_secret_command(sandbox=sandbox, placeholder=placeholder),
            capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip
        if removal.returncode != 0:
            raise RuntimeError(
                f"scoped secret cleanup incomplete: sbx secret rm --placeholder {shlex.quote(placeholder)} "
                f"--sandbox {shlex.quote(sandbox)} -f"
            )


def _network_allowed(host: str) -> bool:
    """Whether the global policy already permits ``host`` (read-only check)."""
    probe = subprocess.run(
        [SBX, "policy", "check", "network", host, "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    allowed = json.loads(probe.stdout)["allowed"]
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
    this function only ever runs on POSIX (real `sbx`-driving callers are
    gated behind `sbx_available()`/`SANDBOX_TESTS_ENABLED` or their own
    documented prerequisites; the unit tests below are Windows-skipped via
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
    what's running inside it. Both `Sandbox.create` and any caller that drives
    `sbx create`/`band-kit provision` directly (e.g. a demo script) need to
    hold this open the same way.
    """
    process, controller_fd = _spawn_pty_process([SBX, "run", "--name", name])
    stopping = threading.Event()

    def drain_output() -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(controller_fd, selectors.EVENT_READ)
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
    try:
        yield
        if process.poll() is not None:
            raise RuntimeError(f"owned sbx run for {name} exited {process.returncode}")
    finally:
        stopping.set()
        reader.join()
        _stop_pty_process(process, controller_fd)


NAME_PREFIX = "band-nevervm"


def sandbox_name(prefix: str = NAME_PREFIX) -> str:
    """A unique sandbox name.

    Allocate it up front when a scoped secret must name the sandbox before
    ``Sandbox.create`` brings it into existence.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class Sandbox:
    """A running Docker Sandbox, with helpers that read as intent.

    Lifecycle is on the class (``Sandbox.create``); driving it wraps the
    ``sbx exec`` plumbing so a test reads what it asks the sandbox to do.
    """

    name: str

    @classmethod
    @contextmanager
    def create(
        cls,
        *,
        kit: Path | str,
        workspace: Path | str,
        name: str | None = None,
        attached: bool = True,
    ) -> Iterator[Sandbox]:
        """Create a positional workload, own its foreground PTY, then remove it."""
        name = name or sandbox_name()
        subprocess.run(
            [SBX, "create", "--name", name, str(kit), str(workspace)],
            capture_output=True,
            text=True,
            check=True,
            timeout=CREATE_TIMEOUT_S,
        )
        # `sbx create` above already succeeded, so `sbx rm` must run on any
        # exception from here on (including one raised while entering
        # attached_run) — otherwise the created sandbox is orphaned.
        try:
            with ExitStack() as stack:
                if attached:
                    stack.enter_context(attached_run(name))
                yield cls(name)
        finally:
            run_sbx_subprocess(
                [SBX, "rm", "-f", name],
                timeout=60,
            )

    @contextmanager
    def owned_run(self) -> Iterator[None]:
        """Own one foreground session, including its exit status."""
        with attached_run(self.name):
            yield

    def stop(self) -> None:
        """Stop the workload before starting a new foreground owner."""
        run_sbx_subprocess([SBX, "stop", self.name], timeout=60)

    def exec(
        self, command: str, *, timeout: int = EXEC_TIMEOUT_S, redact: str | None = None
    ) -> str:
        """Run ``command`` via ``bash -lc`` inside the sandbox; return stripped stdout.

        ``-l`` runs a login shell so the sandbox's login profile is sourced,
        bringing its provisioned PATH and environment into scope (the cert probe,
        for one, reads the proxy/CA variables from there). This is a deliberate
        divergence from the docker sibling's ``bash -c`` — don't collapse it to
        ``-c``.

        Pass ``redact`` when ``command`` embeds a secret: a transport failure then
        raises with the secret masked instead of a ``CalledProcessError`` that
        renders the full argv (secret included) into the test output.
        """
        argv = [SBX, "exec", self.name, "bash", "-lc", command]
        if redact is not None:
            return run_redacting_secret(argv, secret=redact, timeout=timeout).strip()
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return result.stdout.strip()

    def all_process_environ(self) -> str:
        """Every readable process environment in the VM, one VAR=value per line.

        Scans all of ``/proc/<pid>/environ`` rather than assuming the agent is a
        particular pid — in an sbx microVM, pid 1 is the VM init, not the agent,
        and ``sbx exec`` spawns its own processes too. Entries owned by another
        user (e.g. root's pid 1) or racing to exit mid-scan are unreadable; the
        loop's stderr is dropped and it force-exits 0 so those benign
        per-entry failures don't fail the whole ``exec`` — a real ``sbx exec``
        transport error still surfaces (sbx exits non-zero around ``bash``). The
        never-in-VM proof reads this to show the real key is absent from every
        process it can see while the sentinel is present in the agent's.
        """
        # `2>/dev/null` must wrap the *loop* (not each `tr`): the shell's own
        # open-failure on `< "$e"` for an unreadable entry prints before a
        # per-command redirect would apply. `|| true` keeps a partial sweep green.
        return self.exec(
            'for e in /proc/[0-9]*/environ; do tr "\\0" "\\n" < "$e"; done '
            "2>/dev/null || true"
        )

    def band_host_cert(self, host: str) -> str:
        """The TLS cert subject+issuer the sandbox sees for ``host`` — **through
        the sandbox proxy**.

        Uses Python (always in a Python kit; the kit image ships Python) to
        open the connection via ``HTTPS_PROXY``, so it sees the injection MITM
        cert. Connecting *directly* would bypass the proxy and show the real
        upstream cert — hiding the injection. Under injection the output contains
        ``PROXY_CA_MARKER``.
        """
        return self.exec(f"python3 -c {shlex.quote(_CERT_PROBE)} {shlex.quote(host)}")

    def shell_env(self) -> str:
        """The exec shell's own environment (``env``)."""
        return self.exec("env")

    def files_containing(self, secret: str, *, search_paths: Sequence[str]) -> str:
        """Files under ``search_paths`` that contain ``secret`` as a literal,
        newline-joined; ``""`` when none.

        A raw read, not a verdict: the caller pairs this absence check with a
        positive control (see the never-in-VM test) so an empty result can't
        pass vacuously. The command's flag rationale lives in
        ``files_containing_command``. The command embeds ``secret``, so the exec
        is redacted — a transport failure must not print the key it scans for.
        """
        return self.exec(files_containing_command(secret, search_paths), redact=secret)
