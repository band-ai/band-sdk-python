"""Shared E2E settings, skip markers, and types.

Lives in a plain module (not ``conftest.py``) so fixtures, helpers, and tests can
import these symbols without importing from a conftest — which couples modules to
pytest's collection machinery and invites circular imports. ``conftest.py`` holds
only hooks and fixture registration.

Configuration is loaded from ``.env.test`` with E2E-specific overrides from env
vars. E2E tests run adapters against a real Band platform with real (cheap) LLMs;
they verify platform/integration correctness, not LLM output quality, and run
manually only (never in CI):

    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pydantic import ValidationError
from thenvoi_testing.settings import BaseTestSettings

# Load .env.test into os.environ so LLM libraries (langchain, anthropic, etc.)
# can pick up OPENAI_API_KEY, ANTHROPIC_API_KEY, and other keys.
_ENV_TEST_PATH = Path(__file__).parent.parent.parent / ".env.test"
load_dotenv(_ENV_TEST_PATH, override=False)

logger = logging.getLogger(__name__)

# Async callable: name -> (room_id, user_id, user_name). Shared by room fixtures
# and by tests that accept an allocator; defined here so both can import it.
RoomAllocator = Callable[[str], Awaitable[tuple[str, str, str]]]


# =============================================================================
# E2E Settings
# =============================================================================


class E2ESettings(BaseTestSettings):
    """Settings for E2E tests, loaded from .env.test.

    Loads from .env.test and allows E2E-specific overrides via env vars.
    Pydantic BaseSettings automatically maps environment variables to fields
    (e.g. E2E_LLM_MODEL -> e2e_llm_model) with case-insensitive matching.
    """

    class Config:
        env_file = _ENV_TEST_PATH

    band_api_key: str = ""
    band_api_key_2: str = ""
    band_api_key_user: str = ""
    band_base_url: str = "http://localhost:4000"
    band_ws_url: str = "ws://localhost:4000/api/v1/socket/websocket"
    test_agent_id: str = ""
    test_agent_id_2: str = ""

    # E2E-specific settings (override via environment variables)
    e2e_llm_model: str = "gpt-5.4-mini"
    e2e_anthropic_model: str = "claude-haiku-4-5-20251001"
    e2e_timeout: int = 30
    e2e_tests_enabled: bool = False


# =============================================================================
# Skip Markers
# =============================================================================


def _check_e2e_status() -> tuple[bool, str]:
    """Check if E2E tests should be skipped.

    Evaluated once at module import time (when the ``requires_e2e`` marker
    is created). Returns ``(is_disabled, reason)`` so the skip message is
    actionable.
    """
    try:
        settings = E2ESettings()
        if not settings.e2e_tests_enabled:
            return True, "E2E_TESTS_ENABLED is not set to true"
        if not settings.band_api_key:
            return True, "BAND_API_KEY is not set"
        return False, "E2E tests enabled"
    except (ValidationError, ValueError, OSError) as exc:
        logger.warning(
            "E2E settings could not be loaded (missing .env.test?), skipping E2E tests",
            exc_info=True,
        )
        return True, f"E2E settings could not be loaded: {exc}"


_e2e_is_disabled, _e2e_skip_reason = _check_e2e_status()

requires_e2e = pytest.mark.skipif(
    _e2e_is_disabled,
    reason=_e2e_skip_reason or "E2E tests disabled",
)

requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
