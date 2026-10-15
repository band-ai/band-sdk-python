"""The kit trusts the sandbox proxy CA through its own bundle, never the system store.

sbx regenerates /etc/ssl/certs/ca-certificates.crt on every sandbox start, and
a concurrent regeneration from the kit's entrypoint truncates that shared
bundle. These tests pin the contract that replaces it: the entrypoint rebuilds
``$BAND_CA_BUNDLE`` (what ``SSL_CERT_FILE``/``GIT_SSL_CAINFO`` point at) from a
read-only snapshot on every start and never writes the system store.

Gated behind DOCKER_TESTS_ENABLED (see tests/conftest.py).
"""

from __future__ import annotations

import base64
import datetime
import shlex
from collections.abc import Iterator
from dataclasses import dataclass

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

SYSTEM_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
SYSTEM_CA_SOURCES = "/usr/local/share/ca-certificates"
ENTRYPOINT = "/usr/local/bin/band-entrypoint.sh"
# sbx sets these on the process it starts, overriding the image's ENV.
SBX_INJECTED_CA_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
RESTARTS = 20
HTTPS_PORT = 8443
SERVED_URL = f"https://localhost:{HTTPS_PORT}"


@dataclass(frozen=True)
class ProxyCA:
    """A throwaway CA standing in for the sandbox's per-session proxy CA."""

    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey

    @classmethod
    def create(cls, common_name: str) -> ProxyCA:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        cert = (
            _builder(subject=name, issuer=name, public_key=key.public_key())
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        return cls(cert, key)

    @property
    def pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def env_value(self) -> str:
        """The CA as sbx hands it to the container: base64 of the PEM."""
        return base64.b64encode(self.pem.encode()).decode()

    def issue_server_cert(self, hostname: str) -> tuple[str, str]:
        """A leaf for ``hostname`` signed by this CA; returns ``(cert, key)`` PEMs."""
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            _builder(
                subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]),
                issuer=self.cert.subject,
                public_key=key.public_key(),
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
            )
            .sign(self.key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode(), key_pem.decode()


def _builder(
    *, subject: x509.Name, issuer: x509.Name, public_key: ec.EllipticCurvePublicKey
) -> x509.CertificateBuilder:
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
    )


@pytest.fixture(scope="module")
def proxy_ca() -> ProxyCA:
    return ProxyCA.create("Band Kit Test Proxy CA")


@pytest.fixture
def sandboxed_container(
    band_python_kit_image: Image, proxy_ca: ProxyCA
) -> Iterator[Container]:
    """A container started the way sbx starts one: with a proxy CA to trust,
    and sbx's own CA variables pointing at the system bundle."""
    with Container.run(
        band_python_kit_image,
        name_prefix="band-kit-ca-test",
        env={
            "PROXY_CA_CERT_B64": proxy_ca.env_value,
            **dict.fromkeys(SBX_INJECTED_CA_VARIABLES, SYSTEM_BUNDLE),
        },
    ) as container:
        yield container


def certificate_count(container: Container, path: str) -> int:
    return int(container.exec(certificate_count_command(path)))


def write_file(container: Container, path: str, content: str) -> None:
    container.exec(f"cat > {path} <<'PEM'\n{content}PEM")


def serve_https_certified_by(container: Container, ca: ProxyCA) -> None:
    """Serve an empty directory on ``SERVED_URL`` with a cert ``ca`` issued,
    as the proxy does for an intercepted host."""
    cert, key = ca.issue_server_cert("localhost")
    write_file(container, "/tmp/server.pem", cert)
    write_file(container, "/tmp/server.key", key)
    server = (
        "import http.server, ssl\n"
        "ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\n"
        "ctx.load_cert_chain('/tmp/server.pem', '/tmp/server.key')\n"
        f"httpd = http.server.HTTPServer(('localhost', {HTTPS_PORT}),"
        " http.server.SimpleHTTPRequestHandler)\n"
        "httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)\n"
        "httpd.serve_forever()\n"
    )
    container.exec_background(
        f"mkdir -p /tmp/www && cd /tmp/www && python3 -c {shlex.quote(server)}"
    )
    probe = f"import socket; socket.create_connection(('localhost', {HTTPS_PORT}))"
    container.exec(
        f"until python3 -c {shlex.quote(probe)} 2>/dev/null; do sleep 0.2; done"
    )


def outcome(container: Container, command: str) -> str:
    """``command``'s combined output when started through the entrypoint, as
    the launcher and agent are; never raises on failure."""
    return container.exec(f"{ENTRYPOINT} bash -c {shlex.quote(command)} 2>&1 || true")


def test_agent_tls_clients_trust_a_server_the_proxy_ca_certified(
    sandboxed_container: Container, proxy_ca: ProxyCA
) -> None:
    """Python, uv and git each complete the TLS handshake and get an HTTP
    answer (a 404) from a server only the proxy CA vouches for."""
    serve_https_certified_by(sandboxed_container, proxy_ca)
    fetch = f"import urllib.request as r; r.urlopen('{SERVED_URL}/missing')"

    python = outcome(sandboxed_container, f"$BAND_SDK_PYTHON -c {shlex.quote(fetch)}")
    uv = outcome(
        sandboxed_container,
        "echo does-not-exist | $BAND_SDK_UV pip compile - --no-cache"
        f" --index-url {SERVED_URL}/simple/",
    )
    git = outcome(sandboxed_container, f"git ls-remote {SERVED_URL}/repo.git")

    assert "HTTP Error 404" in python
    assert "was not found in the package registry" in uv
    assert "not found" in git


def test_entrypoint_never_writes_the_system_store(
    sandboxed_container: Container, proxy_ca: ProxyCA
) -> None:
    proxy_ca_fragment = "".join(proxy_ca.pem.splitlines()[1:-1])[:64]
    identity = f"stat -c '%i %Y' {SYSTEM_BUNDLE}"
    before = sandboxed_container.exec(identity)

    sandboxed_container.exec(f"sleep 1 && {ENTRYPOINT} true")

    assert sandboxed_container.exec(identity) == before
    holders = sandboxed_container.exec(
        f"grep -rlF {proxy_ca_fragment} {SYSTEM_BUNDLE} {SYSTEM_CA_SOURCES} || true"
    )
    assert holders == ""


def test_restarts_keep_the_bundle_whole_while_the_system_store_is_rewritten(
    sandboxed_container: Container,
) -> None:
    """Re-running the entrypoint while something else keeps regenerating the
    system store, as sbx does on each start, never lets a reader running
    alongside see anything but roots + proxy CA."""
    expected = certificate_count(sandboxed_container, "$BAND_CA_ROOTS") + 1
    sandboxed_container.exec_background(
        "while true; do update-ca-certificates --fresh >/dev/null 2>&1; done"
    )
    sandboxed_container.exec_background(
        f"while true; do {certificate_count_command('$BAND_CA_BUNDLE')}"
        " >> /tmp/reads; done"
    )

    sandboxed_container.exec(f"for i in $(seq {RESTARTS}); do {ENTRYPOINT} true; done")

    reads = sandboxed_container.exec("cat /tmp/reads").split()
    assert len(reads) >= RESTARTS
    assert set(reads) == {str(expected)}


def test_a_start_without_a_proxy_ca_drops_the_previous_sessions_ca(
    sandboxed_container: Container,
) -> None:
    def bundle_is_plain_roots() -> bool:
        verdict = (
            'cmp -s "$BAND_CA_BUNDLE" "$BAND_CA_ROOTS" && echo same || echo differs'
        )
        return sandboxed_container.exec(verdict) == "same"

    assert not bundle_is_plain_roots(), "the sandboxed start should add the proxy CA"

    sandboxed_container.exec(f"env -u PROXY_CA_CERT_B64 {ENTRYPOINT} true")

    assert bundle_is_plain_roots()


def test_a_proxy_ca_that_is_not_a_certificate_fails_the_start_cleanly(
    sandboxed_container: Container,
) -> None:
    not_a_cert = base64.b64encode(b"not a certificate").decode()

    result = sandboxed_container.exec(
        f"PROXY_CA_CERT_B64={not_a_cert} {ENTRYPOINT} true 2>&1; echo exit=$?"
    )

    assert "did not decode to a valid PEM certificate" in result
    assert result.endswith("exit=1")
    bundle_dir = sandboxed_container.exec('ls "$(dirname "$BAND_CA_BUNDLE")"')
    assert bundle_dir == "ca-bundle.pem", (
        "a failed start must not leave a staged bundle"
    )
