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
def connect_proxy(status: bytes) -> Iterator[tuple[str, dict[str, bool]]]:
    """Serve one deliberately split CONNECT response and capture client timing."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    port = listener.getsockname()[1]
    observed = {"sent_tls_before_response_completed": False}

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            request = b""
            while b"\r\n\r\n" not in request:
                request += connection.recv(4096)
            connection.sendall(b"HTTP/1.1 " + status[:3])
            readable, _, _ = select.select([connection], [], [], 0.5)
            observed["sent_tls_before_response_completed"] = bool(readable)
            connection.sendall(status[3:] + b"\r\n\r\n")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", observed
    finally:
        thread.join(timeout=5)
        listener.close()


def run_probe(proxy_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"HTTPS_PROXY": proxy_url, "https_proxy": ""}
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

    assert not observed["sent_tls_before_response_completed"]


def test_cert_probe_rejects_non_successful_connect_response() -> None:
    with connect_proxy(b"407 Proxy Authentication Required") as (proxy_url, observed):
        result = run_probe(proxy_url)

    assert "cert-probe-error: RuntimeError proxy CONNECT refused" in result.stdout
    assert not observed["sent_tls_before_response_completed"]
