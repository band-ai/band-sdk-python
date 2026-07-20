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
# sandbox — the never-in-VM proof asserts the Band host shows this, confirming
# the request rode the injection path rather than a plain tunnel.
PROXY_CA_ISSUER = "Docker Sandboxes Proxy CA"


def sbx_available() -> bool:
    """True when the `sbx` CLI is on PATH (else the sandbox test skips)."""
    return shutil.which(SBX) is not None


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

    def process_environ(self, pid: int = 1) -> str:
        """The NUL-joined ``/proc/<pid>/environ`` of a process in the VM.

        The never-in-VM proof reads this to show the real key is absent even
        from the agent's own process environment (only the sentinel is there).
        """
        return self.exec(f"tr '\\0' '\\n' < /proc/{pid}/environ")

    def band_host_cert_issuer(self, host: str = "app.band.ai") -> str:
        """The TLS cert issuer the sandbox sees for ``host`` (via openssl).

        Under credential injection this is the Docker Sandboxes Proxy CA;
        compare against ``PROXY_CA_ISSUER``.
        """
        return self.exec(
            f"echo | openssl s_client -connect {host}:443 -servername {host} "
            "2>/dev/null | openssl x509 -noout -issuer"
        )

    def real_secret_absent(self, secret: str, *, search_paths: Sequence[str]) -> bool:
        """True when ``secret`` appears nowhere the VM could leak it.

        Checks the environment, ``/proc/1/environ``, and the given paths. The
        caller passes the real value from its own settings and asserts absence;
        the value is never logged.
        """
        env = self.exec("env")
        proc = self.process_environ(1)
        files = self.exec(
            "grep -rIl -- {q} {paths} 2>/dev/null || true".format(
                q=_shell_single_quote(secret), paths=" ".join(search_paths)
            )
        )
        return secret not in env and secret not in proc and not files


def _shell_single_quote(value: str) -> str:
    """POSIX single-quote a value so a real secret is passed to grep safely."""
    return "'" + value.replace("'", "'\\''") + "'"
