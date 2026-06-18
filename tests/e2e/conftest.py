"""E2E test configuration: settings, skip markers, and plugin registration.

E2E tests run adapters against a real Band platform with real (cheap) LLMs.
They verify platform functionality and integration correctness, not LLM output quality.

Run manually only, never in CI/CD:
    E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

Configuration is loaded from .env.test with E2E-specific overrides from env vars.

Fixtures live in concern-focused modules: ``fixtures.clients`` (config + REST/WS
clients), ``fixtures.rooms`` (room allocation + agent identity), ``fixtures.memory``
(memory toolkit). They are imported into this conftest's namespace at the bottom
of the file (not via ``pytest_plugins``, which is only honored in the top-level
conftest) so they stay scoped to ``tests/e2e/``. This module also keeps what tests
import by name — ``E2ESettings``, the ``requires_*`` markers, and the
``RoomAllocator`` type — plus the collection hook.
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


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply E2E-specific markers to all collected tests in this directory.

    1. ``asyncio(loop_scope="session")`` — Fixtures default to the session
       loop via ``asyncio_default_fixture_loop_scope`` in pyproject.toml,
       but test functions default to function-scoped loops. This mismatch
       causes "Future attached to a different loop" errors when tests call
       into session-scoped WS/REST clients.

    2. ``timeout(120)`` — E2E tests interact with live platforms and LLMs,
       so they need more time than the 30s default in pyproject.toml.
       ``pytestmark`` in conftest.py is NOT applied to collected tests;
       markers must be added here or directly on test items.
    """
    e2e_dir = Path(__file__).parent
    session_marker = pytest.mark.asyncio(loop_scope="session")
    timeout_marker = pytest.mark.timeout(120)
    for item in items:
        if Path(item.path).is_relative_to(e2e_dir):
            item.add_marker(session_marker)
            item.add_marker(timeout_marker)


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


# =============================================================================
# Fixture registration
# =============================================================================
# Imported here (rather than via ``pytest_plugins``, which is only honored in the
# top-level conftest) so the fixtures stay scoped to ``tests/e2e/``. The imports
# live at the bottom because the fixture modules import ``E2ESettings`` and
# ``RoomAllocator`` from this module, which must already be defined above.
from tests.e2e.fixtures.clients import (  # noqa: E402, F401
    api_client,
    e2e_config,
    e2e_created_room_ids,
    e2e_room_summary,
    e2e_session_client,
    e2e_session_client_2,
    e2e_user_client,
    ws_client,
)
from tests.e2e.fixtures.memory import memory  # noqa: E402, F401
from tests.e2e.fixtures.rooms import (  # noqa: E402, F401
    adapter_entry,
    e2e_adapter_room,
    e2e_agent_id,
    e2e_agent_info,
    e2e_agent_info_2,
    e2e_fresh_room_allocator,
    e2e_isolation_room_b,
    e2e_parlant_room,
    e2e_room_allocator,
)
