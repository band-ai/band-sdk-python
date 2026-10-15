"""The baseline harness REST client dials IPv4 on Windows only."""

from __future__ import annotations

import sys

import pytest
from band_rest import AsyncRestClient

from tests.e2e.baseline.toolkit.restclient import baseline_rest_client


def _dial_address(client: AsyncRestClient) -> str | None:
    """The address the client's connection pool binds before it dials."""
    http = client._client_wrapper.httpx_client.httpx_client
    return http._transport._pool._local_address


class TestBaselineRestDial:
    @pytest.mark.parametrize(
        ("platform", "local_address"),
        [("win32", "0.0.0.0"), ("linux", None), ("darwin", None)],
    )
    def test_only_windows_pins_ipv4(
        self, monkeypatch: pytest.MonkeyPatch, platform: str, local_address: str | None
    ) -> None:
        monkeypatch.setattr(sys, "platform", platform)
        client = baseline_rest_client(api_key="k", base_url="https://example.invalid")
        assert _dial_address(client) == local_address
