"""Discovery redirect, size, timeout and issuer-chain behavior through HTTP parsing."""

from __future__ import annotations

import json

import pytest
from bandpap.fetch import (
    DiscoveryFailure,
    DiscoveryFetcher,
    FetchLimits,
    discover,
    metadata_url,
)

from tests.pap.kit import HttpNetwork, response


class TestFetch:
    async def test_redirect_and_oauth_chain_with_issuer_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        discovery_payload: dict,
        oauth_payload: dict,
    ) -> None:
        discovery_payload["auth"]["issuer"] = "https://auth.example/company"
        oauth_payload["issuer"] = "https://auth.example/company"
        network = HttpNetwork(
            response(
                status=302, Location="https://shop.example/.well-known/poppy.json"
            ),
            response(json.dumps(discovery_payload).encode()),
            response(json.dumps(oauth_payload).encode()),
        )
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        result = await discover("old.example")
        assert result.document.organization.domain == "shop.example"
        assert network.hosts == ["old.example", "shop.example", "auth.example"]
        assert (
            b"GET /.well-known/oauth-authorization-server/company "
            in network.streams[-1].written
        )
        assert network.streams[-1].hostname == "auth.example"
        assert all(
            b"Authorization:" not in stream.written and b"Cookie:" not in stream.written
            for stream in network.streams
        )
        assert all(stream.closed for stream in network.streams)

    @pytest.mark.parametrize(
        "location",
        [
            "http://internal.example",
            "https://user:pass@host.example",
            "https://host.example/#fragment",
            "\x00https://host.example",
            "https://host.example\\@internal.example",
        ],
    )
    async def test_invalid_redirect_is_never_dialed(
        self, monkeypatch: pytest.MonkeyPatch, location: str
    ) -> None:
        network = HttpNetwork(response(status=302, Location=location))
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(ValueError):
            await DiscoveryFetcher().get_json("https://shop.example")
        assert network.hosts == ["shop.example"]

    async def test_redirect_limit_stops_cycles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        network = HttpNetwork(
            response(status=302, Location="/again"),
            response(status=302, Location="/again"),
        )
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(DiscoveryFailure, match="redirects"):
            await DiscoveryFetcher(FetchLimits(max_redirects=1)).get_json(
                "https://shop.example"
            )
        assert len(network.hosts) == 2

    @pytest.mark.parametrize(
        "wire",
        [
            response(b"x" * 32),
            response(b"[]"),
            response(b"{bad}"),
            response(status=503),
            response(**{"Content-Encoding": "gzip"}),
            response(**{"Content-Type": "text/html"}),
        ],
    )
    async def test_invalid_or_oversized_responses_fail(
        self, monkeypatch: pytest.MonkeyPatch, wire: bytes
    ) -> None:
        network = HttpNetwork(wire)
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(DiscoveryFailure):
            await DiscoveryFetcher(FetchLimits(max_bytes=16)).get_json(
                "https://shop.example"
            )
        assert network.streams[0].closed

    async def test_chunked_body_is_bounded_without_content_length(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        network = HttpNetwork(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n0123456789abcdef\r\n0\r\n\r\n"
        )
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(DiscoveryFailure, match="byte limit"):
            await DiscoveryFetcher(FetchLimits(max_bytes=8)).get_json(
                "https://shop.example"
            )

    async def test_total_deadline_cancels_slow_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        network = HttpNetwork(response(), delay=0.1)
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(TimeoutError):
            await DiscoveryFetcher(FetchLimits(timeout=0.01)).get_json(
                "https://shop.example"
            )
        assert network.streams[0].closed

    async def test_mismatched_domain_stops_before_issuer_fetch(
        self, monkeypatch: pytest.MonkeyPatch, discovery_payload: dict
    ) -> None:
        discovery_payload["organization"]["domain"] = "other.example"
        network = HttpNetwork(response(json.dumps(discovery_payload).encode()))
        monkeypatch.setattr("bandpap.fetch.PublicNetwork", lambda: network)
        with pytest.raises(DiscoveryFailure, match="organization"):
            await discover("shop.example")
        assert network.hosts == ["shop.example"]

    @pytest.mark.parametrize(
        ("issuer", "expected"),
        [
            (
                "https://auth.example",
                "https://auth.example/.well-known/oauth-authorization-server",
            ),
            (
                "https://auth.example/tenant/",
                "https://auth.example/.well-known/oauth-authorization-server/tenant",
            ),
        ],
    )
    def test_rfc8414_path_placement(self, issuer: str, expected: str) -> None:
        assert metadata_url(issuer) == expected
