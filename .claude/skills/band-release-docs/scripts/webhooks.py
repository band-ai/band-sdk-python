"""Send webhook requests and read the message text the posters send."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUEST_TIMEOUT_S = 30.0
# Discord's Cloudflare edge answers urllib's default agent with HTTP 403
# ("error code: 1010"), so every request names itself.
USER_AGENT = (
    "band-sdk-python-release-docs (+https://github.com/band-ai/band-sdk-python)"
)


class WebhookSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    slack_webhook_url: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None


def webhook_url(
    variable: str, secret: SecretStr | None, prefixes: tuple[str, ...]
) -> str:
    """Return the URL ``variable`` holds; it must start with one of ``prefixes``."""
    if secret is None:
        raise ValueError(f"{variable} is required")
    url = secret.get_secret_value()
    if not url.startswith(prefixes):
        raise ValueError(f"{variable} must start with {prefixes[0]}")
    return url


def read_message(path: Path | None) -> str:
    """Read UTF-8 message text from ``path``, or stdin when it is ``None``."""
    raw = path.read_bytes() if path else sys.stdin.buffer.read()
    message = raw.decode("utf-8")
    if not message.strip():
        raise ValueError("message text is required")
    return message


def send(
    service: str, method: str, url: str, payload: dict[str, object] | None = None
) -> str:
    """Send one JSON request and return the body; raise on any non-2xx answer."""
    data = (
        None
        if payload is None
        else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{service} webhook returned HTTP {exc.code}: {body}"
        ) from exc
    # URLError covers connect failures; a read timeout is a bare TimeoutError.
    except OSError as exc:
        raise RuntimeError(f"{service} webhook request failed: {exc}") from exc
