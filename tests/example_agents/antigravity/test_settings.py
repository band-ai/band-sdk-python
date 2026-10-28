"""Build the Antigravity example from explicit environment configuration."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from band.adapters.antigravity_acp import AntigravityACPAdapterConfig


class TestAntigravityExampleSettings:
    def test_missing_provider_credentials_fail_before_startup(
        self, antigravity_example: ModuleType
    ) -> None:
        with pytest.raises(ValueError) as error:
            antigravity_example.build_adapter(antigravity_example.Settings())

        assert str(error.value) == antigravity_example.MISSING_PROVIDER_KEY

    @pytest.mark.parametrize(
        ("environment", "expected_key"),
        [
            (
                {"GEMINI_API_KEY": "gemini-key", "GOOGLE_API_KEY": "google-key"},
                "gemini-key",
            ),
            ({"GOOGLE_API_KEY": "google-key"}, "google-key"),
        ],
    )
    def test_provider_key_selection_is_explicit(
        self,
        antigravity_example: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        environment: dict[str, str],
        expected_key: str,
    ) -> None:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        adapter = antigravity_example.build_adapter(antigravity_example.Settings())

        assert adapter.config == AntigravityACPAdapterConfig(
            gemini_api_key=expected_key
        )

    def test_json_argv_and_session_settings_reach_the_adapter(
        self,
        antigravity_example: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        command = (str(tmp_path / "native runtime" / "server executable"), "--uid=")
        workspace = str(tmp_path / "room workspaces")
        home = str(tmp_path / "private native homes")
        environment = {
            "GEMINI_API_KEY": "provider-key",
            "ANTIGRAVITY_COMMAND": json.dumps(command),
            "ACP_AGENT_CWD": workspace,
            "ANTIGRAVITY_HOME": home,
            "ANTIGRAVITY_MODEL": "advertised-model",
            "ANTIGRAVITY_REASONING_EFFORT": "low",
            "ANTIGRAVITY_APPROVAL_MODE": "auto_decline",
        }
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        adapter = antigravity_example.build_adapter(antigravity_example.Settings())

        assert adapter.config == AntigravityACPAdapterConfig(
            command=command,
            cwd=workspace,
            gemini_api_key="provider-key",
            gemini_home=home,
            model="advertised-model",
            reasoning_effort="low",
            approval_mode="auto_decline",
        )

    def test_provider_keys_are_hidden_from_settings_repr(
        self, antigravity_example: ModuleType
    ) -> None:
        settings = antigravity_example.Settings(
            gemini_api_key="private-gemini-key", google_api_key="private-google-key"
        )

        assert "private-gemini-key" not in repr(settings)
        assert "private-google-key" not in repr(settings)
