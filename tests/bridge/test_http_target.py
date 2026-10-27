"""HTTPTarget behavior."""

from __future__ import annotations

import pytest
from bridge_core.config import (
    HTTPTarget,
)


class TestHTTPTarget:
    def test_accepts_http_url(self) -> None:
        t = HTTPTarget(url="http://localhost:8000/inv")
        assert t.url == "http://localhost:8000/inv"
        assert t.type == "http"

    def test_accepts_https_url(self) -> None:
        t = HTTPTarget(url="https://example.com/inv")
        assert t.url == "https://example.com/inv"

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            HTTPTarget(url="")

    def test_rejects_url_without_scheme(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            HTTPTarget(url="example.com/inv")

    def test_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            HTTPTarget(url="https://x/y", timeout=0)

    @pytest.mark.parametrize(
        "token",
        ["", "   ", "private-bearer\n", " private-bearer", "private bearer", "bearé"],
    )
    def test_rejects_bearer_that_is_not_a_header_token(self, token: str) -> None:
        with pytest.raises(ValueError, match="printable ASCII") as error:
            HTTPTarget(url="https://example.com", bearer_token=token)
        if token.strip():
            assert token.strip() not in str(error.value)

    def test_redacts_bearer(self) -> None:
        target = HTTPTarget(url="https://example.com", bearer_token="private-bearer")
        assert "private-bearer" not in repr(target)

    def test_rejects_misspelled_option_without_echoing_it(self) -> None:
        secret = "private-bearer"
        with pytest.raises(ValueError, match="extra_forbidden") as error:
            HTTPTarget.model_validate(
                {"url": "https://example.com", "bearer_tokn": secret}
            )
        assert secret not in str(error.value)
