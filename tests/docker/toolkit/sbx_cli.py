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

import shlex
import shutil
import subprocess
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

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


def _kit_agent_name(kit: Path | str) -> str:
    """The agent name a sandbox kit declares (its ``spec.yaml`` ``name``).

    A sandbox (agent) kit is invoked as ``sbx create --kit <kit> <name>`` — the
    name is the kit's own, not a generic ``shell`` agent (sbx rejects that combo).
    """
    spec = yaml.safe_load((Path(kit) / "spec.yaml").read_text(encoding="utf-8"))
    return spec["name"]


def _files_containing_command(secret: str, search_paths: Sequence[str]) -> str:
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


@contextmanager
def custom_secret(
    *, host: str, env: str, value: str, placeholder: str
) -> Iterator[None]:
    """Register a global custom-secret injection for ``host``, then remove it.

    The test provisions the secret itself so the value it later asserts-absent is
    exactly the one the proxy would inject — closing the gap where a test checks a
    *different* key than the one actually stored.
    """
    subprocess.run(
        [SBX, "secret", "set-custom", "-g", "--host", host,
         "--env", env, "--placeholder", placeholder, "--value", value],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    try:
        yield
    finally:
        subprocess.run(
            [SBX, "secret", "rm", "-g", "--host", host],
            input="y\n", capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip


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
        agent: str | None = None,
        name_prefix: str = "band-nevervm",
    ) -> Iterator[Sandbox]:
        """`sbx create --kit <kit> <agent> <workspace>`; yield it, then remove.

        For an agent (sandbox) kit the agent name is the kit's own name — a plain
        ``shell`` agent can't be combined with one — so it defaults to the name
        the kit's ``spec.yaml`` declares. Injection config only applies when the
        kit rides ``sbx create --kit`` (not ``sbx kit add``), so the never-in-VM
        proof must create this way.
        """
        agent = agent or _kit_agent_name(kit)
        name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
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

    def exec(self, command: str, *, timeout: int = EXEC_TIMEOUT_S) -> str:
        """Run ``command`` via ``bash -lc`` inside the sandbox; return stripped stdout.

        ``-l`` runs a login shell so the sandbox's login profile is sourced,
        bringing its provisioned PATH and environment into scope (the cert probe,
        for one, reads the proxy/CA variables from there). This is a deliberate
        divergence from the docker sibling's ``bash -c`` — don't collapse it to
        ``-c``.
        """
        result = subprocess.run(
            [SBX, "exec", self.name, "bash", "-lc", command],
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
        user are silently skipped (unreadable), which is fine: the agent runs as
        the exec user, so its environment is included. The never-in-VM proof
        reads this to show the real key is absent from every process it can see
        while the sentinel is present in the agent's.
        """
        return self.exec(
            'for e in /proc/[0-9]*/environ; do tr "\\0" "\\n" < "$e" 2>/dev/null; done'
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
        ``_files_containing_command``.
        """
        return self.exec(_files_containing_command(secret, search_paths))
