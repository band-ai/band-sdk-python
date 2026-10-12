"""A real sandbox keeps verifying TLS across restarts.

On sbx v0.45, every `sbx stop` → `sbx run` rewrote the shared system CA bundle
while the kit's entrypoint rewrote it too, and the result came back truncated:
PyPI verification (dependency sync) and Band verification failed at random.
The first start after `sbx create` was always fine, so a test that never
restarts can't see this.

The workspace runs a probe instead of an agent. The launcher execs it exactly
as it would an agent, after `uv sync --locked` has pulled a dependency from
PyPI. The probe reports what that process sees: the CA bundle its
``SSL_CERT_FILE`` names, and whether TLS verifies for each host. A
placeholder Band secret makes the proxy intercept app.band.ai, so that host
exercises the proxy-CA path and the PyPI hosts exercise the public roots.

Needs the `sbx` CLI on a nested-virtualization host and the local kit image
loaded as `band-python-kit:local` (see the kit README's "Developing the
kit"). Gated behind ``sandbox`` (SANDBOX_TESTS_ENABLED=true); no real Band
credentials needed.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import (
    CREATE_TIMEOUT_S,
    PROXY_CA_MARKER,
    Sandbox,
    custom_secret,
    sandbox_name,
    sbx_available,
)
from tests.paths import KIT_DIR

RESTARTS = 3
# Bounds one start: VM boot, `uv sync`, and the probe's three handshakes.
START_TIMEOUT_S = 180
# All three are in the kit's network baseline: PyPI is what `uv sync --locked`
# reaches, app.band.ai what the agent connects to.
PROBED_HOSTS = ("pypi.org", "files.pythonhosted.org", "app.band.ai")
INTERCEPTED_HOST = "app.band.ai"
REPORT = "tls-report.json"
# Any small wheel works: it only has to make the first `uv sync` hit PyPI.
SYNCED_DEPENDENCY = "six==1.17.0"

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.timeout(CREATE_TIMEOUT_S + (RESTARTS + 1) * START_TIMEOUT_S),
]

PROBE = f"""
import json, os, signal, urllib.error, urllib.request, uuid

def verify(host):
    try:
        urllib.request.urlopen(f"https://{{host}}/", timeout=20)
    except urllib.error.HTTPError:
        pass  # an HTTP status means the TLS handshake verified
    except Exception as exc:
        return f"ERR: {{exc!r}}"
    return "ok"

def certificates(path):
    with open(path) as bundle:
        return bundle.read().count("BEGIN CERTIFICATE")

report = {{
    "session": uuid.uuid4().hex,
    "ssl_cert_file": os.environ.get("SSL_CERT_FILE"),
    "kit_bundle": os.environ.get("BAND_CA_BUNDLE"),
    "bundle_certificates": certificates(os.environ["SSL_CERT_FILE"]),
    "root_certificates": certificates(os.environ["BAND_CA_ROOTS"]),
    "hosts": {{host: verify(host) for host in {PROBED_HOSTS!r}}},
}}
with open({REPORT!r}, "w") as out:
    json.dump(report, out)
signal.pause()
"""


@pytest.fixture
def probe_workspace(tmp_path: Path) -> Path:
    """The echo-agent starter's ``band.yaml`` with the probe as its entrypoint,
    plus a locked project that depends on one PyPI wheel."""
    config = yaml.safe_load((KIT_DIR / "echo-agent" / "band.yaml").read_text())
    config["agent"]["id"] = str(uuid.uuid4())
    (tmp_path / "band.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / config["agent"]["entrypoint"]).write_text(PROBE)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tls-probe"\nversion = "0"\n'
        f'requires-python = ">=3.11"\ndependencies = ["{SYNCED_DEPENDENCY}"]\n'
    )
    subprocess.run(["uv", "lock", "-q"], cwd=tmp_path, check=True, timeout=120)
    return tmp_path


@pytest.fixture
def sandbox(probe_workspace: Path) -> Iterator[Sandbox]:
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    name = sandbox_name()
    with (
        custom_secret(
            sandbox=name,
            host="**.band.ai",
            env="BAND_API_KEY",
            value="not-a-real-key",
            placeholder=PROXY_MANAGED_API_KEY,
        ),
        Sandbox.create(name=name, kit=KIT_DIR, workspace=probe_workspace) as created,
    ):
        yield created


def next_report(
    workspace: Path, sandbox: Sandbox, *, after: str | None
) -> dict[str, Any]:
    """The probe's report from a session other than ``after``, once written."""
    report_path = workspace / REPORT
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if report_path.exists():
            report = json.loads(report_path.read_text() or "{}")
            if report.get("session") not in (None, after):
                return report
        time.sleep(2)
    launcher_log = sandbox.exec(
        "tail -n 30 /home/agent/.local/share/band-kit/logs/launcher.log || true"
    )
    raise TimeoutError(
        f"no new probe report within {START_TIMEOUT_S}s; launcher log:\n{launcher_log}"
    )


def assert_trust_intact(report: dict[str, Any]) -> None:
    assert report["ssl_cert_file"] == report["kit_bundle"]
    assert report["bundle_certificates"] == report["root_certificates"] + 1
    assert report["hosts"] == dict.fromkeys(PROBED_HOSTS, "ok")


def test_tls_trust_survives_sandbox_restarts(
    sandbox: Sandbox, probe_workspace: Path
) -> None:
    report = next_report(probe_workspace, sandbox, after=None)
    assert_trust_intact(report)
    assert PROXY_CA_MARKER in sandbox.band_host_cert(INTERCEPTED_HOST)

    for _ in range(RESTARTS):
        with sandbox.restarted():
            report = next_report(probe_workspace, sandbox, after=report["session"])
            assert_trust_intact(report)
