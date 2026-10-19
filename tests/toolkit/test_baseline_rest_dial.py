"""The baseline harness REST client dials IPv4 on Windows only."""

from __future__ import annotations

import sys

import pytest

from tests.e2e.baseline.toolkit.restclient import baseline_rest_client
from tests.toolkit.dualstack import (
    DUALSTACK_HOST,
    IPV4,
    IPV6,
    dualstack_http,
    install_dualstack_dns,
    only_these_proxies,
)


class TestBaselineRestDial:
    @pytest.mark.parametrize(
        ("platform", "family"),
        [("win32", IPV4), ("linux", IPV6), ("darwin", IPV6)],
    )
    async def test_only_windows_dials_ipv4(
        self, monkeypatch: pytest.MonkeyPatch, platform: str, family: str
    ) -> None:
        only_these_proxies(monkeypatch)
        install_dualstack_dns(monkeypatch)
        async with dualstack_http(ipv4=IPV4.encode(), ipv6=IPV6.encode()) as loopback:
            monkeypatch.setattr(sys, "platform", platform)
            client = baseline_rest_client(
                api_key="k", base_url="https://example.invalid"
            )
            http = client._client_wrapper.httpx_client.httpx_client
            try:
                response = await http.get(
                    f"http://{DUALSTACK_HOST}:{loopback.port}/dial"
                )
            finally:
                await http.aclose()

        assert response.status_code == 200
        assert response.content == family.encode()
        assert loopback.requests[family] == [b"GET /dial HTTP/1.1"]
