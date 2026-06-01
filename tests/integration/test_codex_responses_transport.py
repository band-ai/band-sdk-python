"""Reproduction for INT-501: Codex 401 on the WebSocket Responses API.

The Codex CLI defaults to the WebSocket transport for the OpenAI Responses API
(``wss://api.openai.com/v1/responses``). For API-key auth (``sk-proj`` /
``sk-svcacct``) that endpoint returns ``401 Unauthorized`` even though the REST
``POST /v1/responses`` succeeds (HTTP 200) with the *same* key — the WebSocket
Responses API appears to require separate, account-level access. See:
https://linear.app/thenvoi/issue/INT-501

This test drives the real ``codex`` binary against the real OpenAI API and
classifies each run as WEBSOCKET_401 / SUCCESS / OTHER. It serves two purposes:

1. **Reproduce** — with the default (WebSocket-capable) provider, expect the
   ``responses_websocket ... 401`` failure documented in INT-501.
2. **Validate the fix** — with a custom provider that sets
   ``supports_websockets = false``, Codex falls back to the HTTP/REST Responses
   path (which already works) and the run succeeds.

The 401 is account-gated, so it only reproduces against an affected OpenAI
account (e.g. the band deployment key). The test is therefore opt-in and skips
unless all of the following hold:

* ``CODEX_REPRO_ENABLED=true``
* ``OPENAI_API_KEY`` is set
* a runnable ``codex`` binary is on ``PATH``

Run with::

    CODEX_REPRO_ENABLED=true OPENAI_API_KEY=sk-... \\
        uv run pytest tests/integration/test_codex_responses_transport.py -v -s --no-cov

Inside the deployed image (where ``@openai/codex`` is installed)::

    CODEX_REPRO_ENABLED=true python -m pytest \\
        tests/integration/test_codex_responses_transport.py -v -s
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Model to exercise. Override via CODEX_MODEL; default matches the entrypoint.
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.3-codex")

# A trivial prompt — we only care which transport Codex chooses, not the answer.
PROMPT = "Reply with the single word: pong"

# How long to give a single `codex exec` run before treating it as a failure.
RUN_TIMEOUT_S = float(os.environ.get("CODEX_REPRO_TIMEOUT_S", "90"))

# The fix under evaluation: a custom provider pinned to the HTTP/REST Responses
# transport. This is the exact shape proposed for the deployment config.toml.
REST_PROVIDER_CONFIG = """\
model_provider = "openai_rest"

[model_providers.openai_rest]
name = "OpenAI (REST)"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
"""

# Classification of a `codex exec` run.
WEBSOCKET_401 = "WEBSOCKET_401"
SUCCESS = "SUCCESS"
OTHER = "OTHER"


def _codex_binary() -> str | None:
    """Return a runnable `codex` binary path, or None if unavailable."""
    binary = shutil.which("codex") or shutil.which("codex-cli")
    if not binary:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        # e.g. asdf shim with no node version selected — not actually runnable.
        logger.warning("`codex --version` failed: %s", proc.stderr.strip())
        return None
    logger.info("Using codex: %s (%s)", binary, proc.stdout.strip())
    return binary


_CODEX = _codex_binary()

requires_codex_repro = pytest.mark.skipif(
    not (
        os.environ.get("CODEX_REPRO_ENABLED", "").lower() in ("1", "true", "yes")
        and os.environ.get("OPENAI_API_KEY")
        and _CODEX is not None
    ),
    reason=(
        "INT-501 repro is opt-in: set CODEX_REPRO_ENABLED=true, OPENAI_API_KEY, "
        "and ensure a runnable `codex` binary is on PATH."
    ),
)


def _run_codex(config_toml: str, tmp_path: Path) -> tuple[int, str, str]:
    """Run `codex exec` with an isolated CODEX_HOME containing config_toml.

    Returns (returncode, stdout, stderr). A fresh CODEX_HOME with no auth.json
    forces Codex onto OPENAI_API_KEY (avoiding the OAuth-override footgun in
    openai/codex#15151).
    """
    codex_home = tmp_path / "codex-home"
    (codex_home / "sessions").mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(config_toml, encoding="utf-8")

    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)

    cmd = [
        _CODEX,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-m",
        CODEX_MODEL,
        PROMPT,
    ]
    logger.info("Running: %s (CODEX_HOME=%s)", " ".join(cmd), codex_home)
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        return (-1, out, err + "\n[timed out]")
    return (proc.returncode, proc.stdout, proc.stderr)


def _classify(returncode: int, stdout: str, stderr: str) -> str:
    """Classify a codex run by transport outcome."""
    combined = f"{stdout}\n{stderr}".lower()
    is_ws = "responses_websocket" in combined or (
        "websocket" in combined and "v1/responses" in combined
    )
    if is_ws and "401" in combined:
        return WEBSOCKET_401
    if returncode == 0:
        return SUCCESS
    return OTHER


@requires_codex_repro
class TestCodexResponsesTransport:
    """Reproduce the WebSocket 401 and validate the REST-provider workaround."""

    def test_default_provider_reproduces_websocket_401(self, tmp_path: Path) -> None:
        """Default (WebSocket-capable) provider hits the INT-501 401.

        Empty config.toml -> built-in `openai` provider, which uses the
        WebSocket Responses transport. Against an affected account this emits
        `responses_websocket: failed to connect to websocket: 401`.
        """
        rc, out, err = _run_codex(config_toml="", tmp_path=tmp_path)
        outcome = _classify(rc, out, err)
        logger.info("default-provider outcome=%s rc=%s", outcome, rc)
        logger.info("stdout:\n%s", out)
        logger.info("stderr:\n%s", err)

        if outcome == SUCCESS:
            pytest.skip(
                "Could not reproduce INT-501 here: the default WebSocket "
                "transport succeeded, so this account/key is not WS-gated. "
                "Reproduction requires the affected (band) OpenAI account."
            )
        assert outcome == WEBSOCKET_401, (
            "Expected a WebSocket 401 on the default provider (INT-501), "
            f"got {outcome!r} (rc={rc}).\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        )

    def test_rest_provider_avoids_websocket_401(self, tmp_path: Path) -> None:
        """`supports_websockets = false` provider uses REST and succeeds.

        This is the proposed fix: route through a custom provider pinned to the
        HTTP/REST Responses transport, which already returns 200 for the key.
        """
        rc, out, err = _run_codex(config_toml=REST_PROVIDER_CONFIG, tmp_path=tmp_path)
        outcome = _classify(rc, out, err)
        logger.info("rest-provider outcome=%s rc=%s", outcome, rc)
        logger.info("stdout:\n%s", out)
        logger.info("stderr:\n%s", err)

        assert outcome != WEBSOCKET_401, (
            "REST provider (supports_websockets=false) still attempted the "
            "WebSocket transport and got 401 — the fix did not take effect.\n"
            f"--- stdout ---\n{out}\n--- stderr ---\n{err}"
        )
        assert rc == 0, (
            "REST provider run did not succeed (rc="
            f"{rc}). It avoided the WS 401 but failed for another reason — "
            f"inspect output.\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        )
