"""Thin `sbx`-CLI seam for the never-in-VM sandbox E2E.

Mirrors :class:`tests.docker.toolkit.docker_cli.Container`, but drives the
**sandbox** flow — `sbx create --kit` / `sbx exec` / `sbx rm` — so requests ride
the host-side proxy that injects the real credentials. That is what a bare
`docker run` cannot exercise, and what the never-in-VM proof needs.

Only usable on a Docker-Sandbox-capable host with the `sbx` CLI; the test gates
on the ``sandbox`` marker (``SANDBOX_TESTS_ENABLED=true``), so this module is
never imported on an ordinary unit run.
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


def sbx_available() -> bool:
    """True when the `sbx` CLI is on PATH (else the sandbox test skips)."""
    return shutil.which(SBX) is not None


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
        agent: str = "shell",
        name_prefix: str = "band-nevervm",
    ) -> Iterator[Sandbox]:
        """`sbx create --kit <kit> <agent> <workspace>`; yield it, then remove.

        Injection config only applies when the kit rides ``sbx create --kit``
        (not ``sbx kit add``) — so the never-in-VM proof must create this way.
        """
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
            subprocess.run([SBX, "rm", "-f", name], capture_output=True, check=False)

    def exec(self, command: str, *, timeout: int = EXEC_TIMEOUT_S) -> str:
        """Run ``command`` via `bash -lc` inside the sandbox; return stripped stdout."""
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

        curl honors ``HTTPS_PROXY``, so it sees the injection MITM cert. A raw
        ``openssl s_client -connect`` would connect *directly*, bypass the proxy,
        and show the real upstream cert — hiding the injection (verified the hard
        way). Under injection the output contains ``PROXY_CA_MARKER``.
        """
        return self.exec(
            f"curl -sv --max-time 15 https://{host}/ 2>&1 "
            "| grep -iE 'subject:|issuer:' | head -2"
        )

    def real_secret_absent(self, secret: str, *, search_paths: Sequence[str]) -> bool:
        """True when ``secret`` appears nowhere the VM could leak it.

        Checks the exec shell's environment, every readable ``/proc/*/environ``,
        and the given paths. The caller passes the real value from its own
        settings and asserts absence; the value is never logged.
        """
        env = self.exec("env")
        proc = self.all_process_environ()
        # -F: match the secret as a literal, not a regex — a key with '.' or
        # other metacharacters must not over- or under-match in an absence proof.
        paths = " ".join(shlex.quote(path) for path in search_paths)
        files = self.exec(
            f"grep -rIlF -- {shlex.quote(secret)} {paths} 2>/dev/null || true"
        )
        return secret not in env and secret not in proc and not files
