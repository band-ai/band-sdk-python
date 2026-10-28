"""Select Antigravity's API key and exact native command for baseline runs."""

from __future__ import annotations

import sys

from tests.e2e.baseline.settings import BaselineSettings


def antigravity_api_key(settings: BaselineSettings) -> str:
    """Prefer the native Gemini key, accepting Google's shared key as fallback."""
    creds = settings.llm_credentials
    return creds.gemini_api_key or creds.google_api_key


def antigravity_command(settings: BaselineSettings) -> tuple[str, ...]:
    """Resolve an explicit argv override or the SDK's platform-specific default."""
    from band.adapters.antigravity_acp import (  # noqa: PLC0415 -- optional acp extra
        default_antigravity_command,
    )

    return settings.backends.antigravity_command or default_antigravity_command(
        sys.platform
    )
