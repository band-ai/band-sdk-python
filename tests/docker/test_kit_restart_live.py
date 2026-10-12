"""A real sandbox keeps verifying TLS across restarts.

On sbx v0.45, every `sbx stop` → `sbx run` rewrote the shared system CA bundle
while the kit's entrypoint rewrote it too, and the result came back truncated:
PyPI verification (dependency sync) and Band verification failed at random.
The first start after `sbx create` was always fine, so a test that never
restarts can't see this.

Needs the `sbx` CLI on a nested-virtualization host and the local kit image
loaded as `band-python-kit:local` (see the kit README's "Developing the kit").
Gated behind ``sandbox`` (SANDBOX_TESTS_ENABLED=true); no Band credentials
needed.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.docker.toolkit.docker_cli import certificate_count_command
from tests.docker.toolkit.sbx_cli import (
    CREATE_TIMEOUT_S,
    EXEC_TIMEOUT_S,
    Sandbox,
    sbx_available,
)
from tests.paths import KIT_DIR

RESTARTS = 3
# All three are in the kit's network baseline: PyPI is what `uv sync --locked`
# reaches, app.band.ai what the agent connects to.
VERIFIED_HOSTS = ("pypi.org", "files.pythonhosted.org", "app.band.ai")

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.timeout(CREATE_TIMEOUT_S + RESTARTS * 4 * EXEC_TIMEOUT_S),
]

_VERIFY_PROBE = """
import sys, urllib.error, urllib.request
for host in sys.argv[1:]:
    try:
        urllib.request.urlopen(f"https://{host}/", timeout=20)
    except urllib.error.HTTPError:
        pass  # an HTTP status means the TLS handshake verified
    print(host)
"""


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    with Sandbox.create(kit=KIT_DIR, workspace=tmp_path) as created:
        yield created


def certificate_count(sandbox: Sandbox, path: str) -> int:
    return int(sandbox.exec(certificate_count_command(path)))


def verified_hosts(sandbox: Sandbox) -> tuple[str, ...]:
    """The hosts whose TLS chain verifies against the agent's default trust."""
    hosts = " ".join(VERIFIED_HOSTS)
    output = sandbox.exec(f"python3 -c {shlex.quote(_VERIFY_PROBE)} {hosts} || true")
    return tuple(output.split())


def test_tls_trust_survives_sandbox_restarts(sandbox: Sandbox) -> None:
    expected = certificate_count(sandbox, "$BAND_CA_ROOTS") + 1

    for _ in range(RESTARTS):
        with sandbox.restarted():
            assert certificate_count(sandbox, "$SSL_CERT_FILE") == expected
            assert verified_hosts(sandbox) == VERIFIED_HOSTS
