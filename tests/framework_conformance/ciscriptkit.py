"""Shared helpers for the ``.github/scripts`` guards: the POSIX-shell marker and a
local stand-in for GitHub's OIDC endpoint and the OpenCode Zen token exchange."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from tests.paths import CI_SCRIPTS

# POSIX-shell only. On Windows, `shutil.which("bash")` finds System32\bash.exe —
# the WSL launcher, not a shell — which on a runner with no WSL distro installed
# prints a UTF-16 "no installed distributions" notice and exits 1. So a
# which()-based guard does not skip, it just fails confusingly. The scripts under
# test run under a POSIX bash in CI, which is their real contract.
posix_shell_only = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX bash (Windows `bash` is the WSL launcher)",
)

ZEN_SIGN_IN = CI_SCRIPTS / "opencode-zen-sign-in.sh"
OIDC_TOKEN = "github-oidc-jwt"
ZEN_KEY = "zen-short-lived-key"
OIDC_OK = (200, {"value": OIDC_TOKEN})
UNREACHED = (500, {})
SIGN_IN_FAILED = "OpenCode Zen sign-in failed: "


@dataclass
class SignInEndpoints:
    """A local stand-in for GitHub's OIDC endpoint and the Zen token exchange."""

    oidc: tuple[int, dict[str, object]]
    exchange: tuple[int, dict[str, object]]
    oidc_requests: list[tuple[str, str | None]] = field(default_factory=list)
    exchanges: list[dict[str, list[str]]] = field(default_factory=list)


@contextmanager
def _serving(endpoints: SignInEndpoints) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            endpoints.oidc_requests.append(
                (self.path, self.headers.get("Authorization"))
            )
            self._reply(endpoints.oidc)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            endpoints.exchanges.append(parse_qs(body))
            self._reply(endpoints.exchange)

        def _reply(self, reply: tuple[int, dict[str, object]]) -> None:
            status, payload = reply
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def sign_in(
    tmp_path: Path, endpoints: SignInEndpoints
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the real sign-in script against ``endpoints``; return it and $GITHUB_ENV."""
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    with _serving(endpoints) as base:
        result = subprocess.run(
            ["bash", str(ZEN_SIGN_IN)],
            env={
                "PATH": os.environ["PATH"],
                "OPENCODE_ZEN_CLIENT_ID": "client-1",
                "ACTIONS_ID_TOKEN_REQUEST_URL": f"{base}/oidc?v=1",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "OPENCODE_ZEN_TOKEN_URL": f"{base}/token",
                "GITHUB_ENV": str(github_env),
            },
            capture_output=True,
            text=True,
            check=False,
        )
    return result, github_env.read_text()
