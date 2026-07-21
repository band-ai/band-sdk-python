"""Regression coverage for the sandbox proxy certificate probe."""

from __future__ import annotations

import os
import select
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from tests.docker.toolkit.sbx_cli import _CERT_PROBE


@contextmanager
def connect_proxy(status: bytes) -> Iterator[tuple[str, dict[str, object]]]:
    """Serve one deliberately split CONNECT response and capture client timing.

    ``observed`` doubles as a positive control: ``received_connect`` records that
    the probe actually reached the proxy with a well-formed CONNECT, and ``error``
    surfaces a server-thread failure (e.g. ``accept()`` timing out because the
    probe never connected) that would otherwise be swallowed on the daemon thread
    — without both, the timing assertion could pass vacuously over a probe that
    never ran.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    port = listener.getsockname()[1]
    observed: dict[str, object] = {
        "received_connect": False,
        "sent_tls_before_response_completed": False,
        "error": None,
    }

    def serve() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                observed["received_connect"] = request.startswith(b"CONNECT ")
                connection.sendall(b"HTTP/1.1 " + status[:3])
                readable, _, _ = select.select([connection], [], [], 0.5)
                observed["sent_tls_before_response_completed"] = bool(readable)
                connection.sendall(status[3:] + b"\r\n\r\n")
        except OSError as exc:  # accept()/recv() timeout or reset: probe never arrived
            observed["error"] = exc

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", observed
    finally:
        thread.join(timeout=5)
        listener.close()


def run_probe(proxy_url: str) -> subprocess.CompletedProcess[str]:
    # Drop any casing of the proxy var first: on Windows os.environ keys are
    # normalized to uppercase, so merging in both "HTTPS_PROXY" and
    # "https_proxy" produces two distinct keys in the plain dict `|` yields,
    # and the child process can end up reading the empty lowercase one.
    env = {k: v for k, v in os.environ.items() if k.upper() != "HTTPS_PROXY"}
    env["HTTPS_PROXY"] = proxy_url
    return subprocess.run(
        [sys.executable, "-c", _CERT_PROBE, "band.example.test"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=5,
    )


def test_cert_probe_waits_for_all_connect_headers_before_tls() -> None:
    with connect_proxy(b"200 Connection Established") as (proxy_url, observed):
        run_probe(proxy_url)

    # Positive control: the probe reached the proxy and sent CONNECT (so the
    # timing assertion isn't passing over a probe that never connected).
    assert observed["error"] is None
    assert observed["received_connect"]
    assert not observed["sent_tls_before_response_completed"]


def test_cert_probe_rejects_non_successful_connect_response() -> None:
    with connect_proxy(b"407 Proxy Authentication Required") as (proxy_url, observed):
        result = run_probe(proxy_url)

    assert observed["error"] is None
    assert observed["received_connect"]
    assert "cert-probe-error: RuntimeError proxy CONNECT refused" in result.stdout
    assert not observed["sent_tls_before_response_completed"]
