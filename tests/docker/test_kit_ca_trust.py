"""The kit trusts the sandbox proxy CA through its own bundle, never the system store.

sbx regenerates /etc/ssl/certs/ca-certificates.crt on every sandbox start. A
second, concurrent regeneration from the kit's entrypoint left that shared
bundle truncated after a restart. These tests pin the replacement contract: the
entrypoint builds ``$BAND_CA_BUNDLE`` (what ``SSL_CERT_FILE`` points at) from a
read-only snapshot, so concurrent rewrites of the system store can't reach it.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py).
"""

from __future__ import annotations

import base64
import datetime
from collections.abc import Iterator

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.docker.toolkit.docker_cli import (
    BUILD_TIMEOUT_S,
    Container,
    Image,
    certificate_count_command,
)

pytestmark = [pytest.mark.docker_build, pytest.mark.timeout(BUILD_TIMEOUT_S + 120)]

PROXY_CA_NAME = "Band Kit Test Proxy CA"
SYSTEM_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
ENTRYPOINT = "/usr/local/bin/band-entrypoint.sh"
RESTARTS = 20


def self_signed_ca_pem(common_name: str) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="module")
def proxy_ca_pem() -> str:
    return self_signed_ca_pem(PROXY_CA_NAME)


@pytest.fixture
def sandboxed_container(
    band_python_kit_image: Image, proxy_ca_pem: str
) -> Iterator[Container]:
    """A container started the way sbx starts one: with a proxy CA to trust."""
    with Container.run(
        band_python_kit_image,
        name_prefix="band-kit-ca-test",
        env={"PROXY_CA_CERT_B64": base64.b64encode(proxy_ca_pem.encode()).decode()},
    ) as container:
        yield container


def certificate_count(container: Container, path: str) -> int:
    return int(container.exec(certificate_count_command(path)))


def default_tls_trusts(container: Container, common_name: str) -> bool:
    """Whether the agent user's default Python TLS context trusts ``common_name``."""
    probe = (
        "import ssl, sys\n"
        "names = {v for c in ssl.create_default_context().get_ca_certs()\n"
        "         for rdn in c['subject'] for k, v in rdn if k == 'commonName'}\n"
        f"print({common_name!r} in names)"
    )
    return (
        container.run_python(probe, interpreter="$BAND_SDK_PYTHON", user="agent")
        == "True"
    )


def test_agent_tls_trusts_the_proxy_ca_and_public_roots(
    sandboxed_container: Container,
) -> None:
    roots = certificate_count(sandboxed_container, "$BAND_CA_ROOTS")

    assert roots > 100, "the build-time root snapshot should hold the public roots"
    assert certificate_count(sandboxed_container, "$SSL_CERT_FILE") == roots + 1
    assert default_tls_trusts(sandboxed_container, PROXY_CA_NAME)


def test_entrypoint_leaves_the_system_store_untouched(
    sandboxed_container: Container,
) -> None:
    """Compared before/after rather than against the snapshot: a container
    runtime may add its own roots to the system store at start (OrbStack does)."""
    before = sandboxed_container.exec(f"sha256sum < {SYSTEM_BUNDLE}")
    sandboxed_container.exec(f"{ENTRYPOINT} true")
    after = sandboxed_container.exec(f"sha256sum < {SYSTEM_BUNDLE}")

    assert after == before


def test_restarts_keep_the_bundle_whole_while_the_system_store_is_rewritten(
    sandboxed_container: Container,
) -> None:
    """Re-running the entrypoint while something else keeps regenerating the
    system store, as sbx does on each start, must yield the same bundle every
    time."""
    roots = certificate_count(sandboxed_container, "$BAND_CA_ROOTS")
    sandboxed_container.exec_background(
        "while true; do update-ca-certificates --fresh >/dev/null 2>&1; done"
    )

    counts = sandboxed_container.exec(
        f"for i in $(seq {RESTARTS}); do {ENTRYPOINT} true; "
        f"{certificate_count_command('$SSL_CERT_FILE')}; done"
    ).split()

    assert counts == [str(roots + 1)] * RESTARTS


def test_a_proxy_ca_that_is_not_a_certificate_fails_the_start(
    band_python_kit_image: Image,
) -> None:
    result = Container.run_to_exit(
        band_python_kit_image,
        env={"PROXY_CA_CERT_B64": base64.b64encode(b"not a certificate").decode()},
        command=["true"],
    )

    assert result.returncode == 1
    assert "did not decode to a valid PEM certificate" in result.stderr
