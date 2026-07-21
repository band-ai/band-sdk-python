"""Thin `sbx`-CLI seam for the never-in-VM sandbox E2E.

Mirrors :class:`tests.docker.toolkit.docker_cli.Container`, but drives the
**sandbox** flow — `sbx create --kit` / `sbx exec` / `sbx rm` — so requests ride
the host-side proxy that injects the real credentials. That is what a bare
`docker run` cannot exercise, and what the never-in-VM proof needs.

The sandbox-driving methods need a Docker-Sandbox-capable host with the `sbx`
CLI, and run only under the ``sandbox``-marked proof
(``SANDBOX_TESTS_ENABLED=true``). The module itself imports cleanly (stdlib +
yaml), so its pure helpers are unit-tested in CI: the agent-name derivation and
the absence-search command in ``tests/docker/test_nevervm_contracts.py``, and
the proxy cert probe in ``tests/docker/test_sbx_cli.py``.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SBX = "sbx"
# `sbx create` provisions a microVM (image pull + boot); generous like the
# docker image build ceiling in docker_cli.
CREATE_TIMEOUT_S = 600
EXEC_TIMEOUT_S = 120

# The issuer a credential-injected (MITM) TLS connection presents inside a
# sandbox — the never-in-VM proof asserts the Band host's cert shows this,
# confirming the request rode the injection path rather than a plain tunnel.
# Verified live: the injected cert reads `subject: O=Docker Sandboxes; CN=<host>`,
# `issuer: ... Docker Sandboxes Proxy CA`, so this substring matches either line.
PROXY_CA_MARKER = "Docker Sandboxes"

# Read the TLS peer cert the sandbox sees for a host *through* HTTPS_PROXY, so an
# injected (MITM) connection reveals the Docker Sandboxes CA in its issuer. Uses
# only the stdlib (the kit image has Python but no curl). Host is argv[1].
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


def kit_agent_name(kit: Path | str) -> str:
    """The agent name a sandbox kit declares (its ``spec.yaml`` ``name``).

    A sandbox (agent) kit is invoked as ``sbx create --kit <kit> <name>`` — the
    name is the kit's own, not a generic ``shell`` agent (sbx rejects that combo).
    """
    return _kit_spec(kit)["name"]


def kit_baseline_hosts(kit: Path | str) -> frozenset[str]:
    """The hosts the kit reaches out of the box (``caps.network.allow``).

    The sandbox proxy denies every other host by default, so a Band deployment
    outside this set (any non-prod target) needs an explicit ``allow_network``
    before the agent can connect. Read from the kit's own spec so the two never
    drift.
    """
    caps = _kit_spec(kit).get("caps") or {}
    network = caps.get("network") or {}
    return frozenset(network.get("allow") or [])


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
    *, sandbox: str, host: str, env: str, value: str, placeholder: str
) -> list[str]:
    """`sbx secret set-custom` argv, scoped to one sandbox (never global ``-g``)."""
    return [SBX, "secret", "set-custom", sandbox, "--host", host,
            "--env", env, "--placeholder", placeholder, "--value", value]  # fmt: skip


def remove_custom_secret_command(*, sandbox: str, host: str) -> list[str]:
    """`sbx secret rm` argv for the scoped secret; ``-f`` skips the prompt."""
    return [SBX, "secret", "rm", sandbox, "--host", host, "-f"]


def run_redacting_secret(argv: list[str], *, secret: str, timeout: int = 60) -> str:
    """Run a secret-bearing command, redacting ``secret`` from any error.

    ``subprocess`` renders the failing argv into ``CalledProcessError``, so a
    routine failure (daemon down, auth, a dead sandbox) would spill the
    credential into the traceback and captured test logs — whether the secret is
    a whole argument (``--value <secret>``) or embedded inside one (a grep
    command quoting it). Run unchecked and raise a redacted error instead;
    stdout is returned on success, so the secret is never surfaced either way.
    """
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode == 0:
        return result.stdout
    redacted = " ".join(shlex.quote(arg).replace(secret, "***") for arg in argv)
    detail = (result.stderr or result.stdout or "").replace(secret, "***").strip()
    raise RuntimeError(f"{redacted} failed (exit {result.returncode}): {detail}")


@contextmanager
def custom_secret(
    *, sandbox: str, host: str, env: str, value: str, placeholder: str
) -> Iterator[None]:
    """Inject ``value`` for ``host`` scoped to ``sandbox``, then remove it.

    Scoped to the one sandbox on purpose — never global (``-g``). The kit README
    registers the Band key globally for real use, so a global test secret would
    *overwrite and then delete* the operator's own ``**.band.ai`` credential and
    disrupt their running sandboxes. The secret is set before ``sbx create`` so
    the placeholder is baked into the VM's ``env`` at startup (a scoped set for a
    not-yet-created sandbox persists and returns cleanly — verified against sbx
    0.35.0).

    The test provisions the secret itself, so the value it later asserts-absent
    is exactly the one the proxy would inject — closing the gap where a test
    checks a *different* key than the one actually stored.
    """
    run_redacting_secret(
        set_custom_secret_command(
            sandbox=sandbox, host=host, env=env, value=value, placeholder=placeholder
        ),
        secret=value,
    )
    try:
        yield
    finally:
        removal = subprocess.run(
            remove_custom_secret_command(sandbox=sandbox, host=host),
            capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip
        if removal.returncode != 0:
            # A failed removal leaves the real key in sbx's secret store (scoped
            # to a torn-down sandbox, so never applied — but still stored). Don't
            # raise from teardown; surface it so the operator can clean up.
            logger.warning(
                "failed to remove the scoped Band secret for sandbox %s (exit %s); "
                "remove it manually: sbx secret rm %s --host %s -f",
                sandbox,
                removal.returncode,
                sandbox,
                host,
            )


def _network_allowed(host: str) -> bool:
    """Whether the global policy already permits ``host`` (read-only check)."""
    probe = subprocess.run(
        [SBX, "policy", "check", "network", host],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return probe.returncode == 0


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
            # This is a *global* rule (sbx can't scope it before create), so a
            # failed removal (e.g. an expired session) leaves it in force for the
            # non-prod host. Don't raise from teardown; surface it so the operator
            # can revoke it. (`rm --resource` matches the bare host sbx stores, so
            # a mismatch is not the failure mode — a lost session is.)
            logger.warning(
                "failed to remove the global network allow rule for %s (exit %s); "
                "remove it manually: sbx policy rm network --resource %s",
                host,
                removal.returncode,
                host,
            )


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
        agent: str | None = None,
    ) -> Iterator[Sandbox]:
        """`sbx create --kit <kit> <agent> <workspace>`; yield it, then remove.

        Pass ``name`` when it was allocated up front (so a scoped secret could be
        provisioned before creation); otherwise a unique one is generated. For an
        agent (sandbox) kit the agent name is the kit's own name — a plain
        ``shell`` agent can't be combined with one — so it defaults to the name
        the kit's ``spec.yaml`` declares. Injection config only applies when the
        kit rides ``sbx create --kit`` (not ``sbx kit add``), so the never-in-VM
        proof must create this way.
        """
        agent = agent or kit_agent_name(kit)
        name = name or sandbox_name()
        subprocess.run(
            [SBX, "create", "--name", name, "--kit", str(kit), agent, str(workspace)],
            capture_output=True,
            text=True,
            check=True,
            timeout=CREATE_TIMEOUT_S,
        )
        try:
            yield cls(name)
        finally:
            subprocess.run(
                [SBX, "rm", "-f", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )

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

        Uses Python (always in a Python kit; the kit image ships no ``curl``) to
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
