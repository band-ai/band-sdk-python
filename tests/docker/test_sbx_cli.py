from __future__ import annotations

from tests.docker.sbxcli.kit import (
    connect_proxy,
    run_probe,
)


class TestProxyCertificateProbe:
    def test_cert_probe_waits_for_all_connect_headers_before_tls(self) -> None:
        with connect_proxy(b"200 Connection Established") as (proxy_url, observed):
            run_probe(proxy_url)

        # Positive control: the probe reached the proxy and sent CONNECT (so the
        # timing assertion isn't passing over a probe that never connected).
        assert observed["error"] is None
        assert observed["received_connect"]
        assert not observed["sent_tls_before_response_completed"]

    def test_cert_probe_rejects_non_successful_connect_response(self) -> None:
        with connect_proxy(b"407 Proxy Authentication Required") as (
            proxy_url,
            observed,
        ):
            result = run_probe(proxy_url)

        assert observed["error"] is None
        assert observed["received_connect"]
        assert "cert-probe-error: RuntimeError proxy CONNECT refused" in result.stdout
        assert not observed["sent_tls_before_response_completed"]
