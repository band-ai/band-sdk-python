from __future__ import annotations

import importlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tests.e2e.conftest import (
    DEFAULT_E2E_ADAPTERS,
    E2ESettings,
    _PROVIDER_BASE_URL_ENV_VARS,
    _RateLimitedObjectProxy,
    _cleared_provider_base_url_env_vars,
)


def test_e2e_settings_reject_placeholder_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_LLM_MODEL", "gpt-X.X-mini")

    with pytest.raises(ValidationError, match="concrete model name"):
        E2ESettings()


def test_e2e_settings_reject_placeholder_anthropic_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_ANTHROPIC_MODEL", "claude-placeholder")

    with pytest.raises(ValidationError, match="concrete model name"):
        E2ESettings()


def test_default_e2e_adapter_matrix_excludes_crewai_lane() -> None:
    assert "crewai" not in DEFAULT_E2E_ADAPTERS
    assert "pydantic_ai" in DEFAULT_E2E_ADAPTERS
    assert "codex" in DEFAULT_E2E_ADAPTERS


def test_provider_base_url_overrides_are_scoped_for_live_e2e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _PROVIDER_BASE_URL_ENV_VARS:
        monkeypatch.setenv(name, "http://localhost:9999")

    with _cleared_provider_base_url_env_vars():
        for name in _PROVIDER_BASE_URL_ENV_VARS:
            assert name not in os.environ

    for name in _PROVIDER_BASE_URL_ENV_VARS:
        assert os.environ[name] == "http://localhost:9999"


async def test_rate_limited_rest_proxy_waits_before_nested_api_calls() -> None:
    endpoint = SimpleNamespace(fetch=AsyncMock(return_value="ok"))
    limiter = SimpleNamespace(wait=AsyncMock())
    proxy = _RateLimitedObjectProxy(SimpleNamespace(endpoint=endpoint), limiter)

    result = await proxy.endpoint.fetch("room-1")

    assert result == "ok"
    limiter.wait.assert_awaited_once_with()
    endpoint.fetch.assert_awaited_once_with("room-1")


def test_crewai_factory_skips_without_crewai_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e.adapters import conftest as adapter_conftest

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(adapter_conftest, "_is_conflicting_crewai_lane", lambda: True)

    with pytest.raises(pytest.skip.Exception, match="dev-crewai lane"):
        adapter_conftest.create_crewai_adapter(E2ESettings())


def test_parlant_module_import_does_not_mutate_agent_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THENVOI_API_KEY", "thnv_u_test_user_key")
    monkeypatch.delenv("TEST_AGENT_ID", raising=False)
    monkeypatch.delenv("THENVOI_AGENT_ID", raising=False)
    monkeypatch.delenv("THENVOI_API_KEY_USER", raising=False)

    module = importlib.import_module("tests.e2e.adapters.test_parlant")
    importlib.reload(module)

    assert "TEST_AGENT_ID" not in os.environ
    assert "THENVOI_AGENT_ID" not in os.environ
    assert "THENVOI_API_KEY_USER" not in os.environ
    assert os.environ["THENVOI_API_KEY"] == "thnv_u_test_user_key"
