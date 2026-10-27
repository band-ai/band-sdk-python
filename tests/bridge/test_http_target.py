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

    @pytest.mark.parametrize("token", ["", "   "])
    def test_rejects_blank_bearer(self, token: str) -> None:
        with pytest.raises(ValueError, match="printable ASCII"):
            HTTPTarget(url="https://example.com", bearer_token=token)

    @pytest.mark.parametrize(
        "token, secret",
        [
            ("private-bearer\n", "private-bearer"),
            (" private-bearer", "private-bearer"),
            ("private bearer", "private bearer"),
            ("private-béarer", "private-béarer"),
        ],
    )
    def test_rejects_bearer_that_is_not_a_header_token_without_echoing_it(
        self, token: str, secret: str
    ) -> None:
        with pytest.raises(ValueError, match="printable ASCII") as error:
            HTTPTarget(url="https://example.com", bearer_token=token)
        assert secret not in str(error.value)

    def test_accepts_token68_bearer(self) -> None:
        token = "aaa.bbb-ccc_dd~e+f/g=="
        target = HTTPTarget(url="https://example.com", bearer_token=token)
        assert target.bearer_token is not None
        assert target.bearer_token.get_secret_value() == token

    def test_rejects_url_userinfo_with_bearer(self) -> None:
        with pytest.raises(ValueError, match="userinfo") as error:
            HTTPTarget(
                url="https://user:pass@example.com", bearer_token="private-bearer"
            )
        assert "pass" not in str(error.value)
        assert "private-bearer" not in str(error.value)

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
