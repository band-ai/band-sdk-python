"""Antigravity requires a reachable native CLI and actual API-key auth."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from tests.baseline.adapter import Adapter
from tests.e2e.baseline.settings import Backends, BaselineSettings, LLMCredentials
from tests.e2e.baseline.toolkit.adapters import spec_for
from tests.e2e.baseline.toolkit.antigravity import (
    antigravity_api_key,
    antigravity_command,
)
from tests.e2e.baseline.toolkit.ci_lanes import ci_lanes
from tests.e2e.baseline.toolkit.deps import Dep, Lane, dep_lane, requirement_reason

pytest.importorskip("acp")

from band.adapters.antigravity_acp import default_antigravity_command


class TestAntigravityRequirements:
    @pytest.mark.parametrize(
        ("gemini_key", "google_key", "expected_key"),
        [
            ("gemini-key", "google-key", "gemini-key"),
            ("", "google-key", "google-key"),
            ("", "", ""),
        ],
    )
    def test_native_key_selection(
        self, gemini_key: str, google_key: str, expected_key: str
    ) -> None:
        settings = BaselineSettings(
            llm_credentials=LLMCredentials(
                gemini_api_key=gemini_key, google_api_key=google_key
            )
        )

        assert antigravity_api_key(settings) == expected_key

    def test_explicit_executable_with_spaces_is_available(
        self, native_executable: Path
    ) -> None:
        command = (str(native_executable), "argument with spaces")
        settings = BaselineSettings(
            backends=Backends(antigravity_command=command),
            llm_credentials=LLMCredentials(gemini_api_key="key", google_api_key=""),
        )

        assert antigravity_command(settings) == command
        assert requirement_reason(Dep.ANTIGRAVITY, settings) is None

    def test_default_command_uses_sdk_platform_selection(
        self, native_executable: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = default_antigravity_command(sys.platform)
        executable = native_executable.parent / command[0]
        shutil.copy2(sys.executable, executable)
        monkeypatch.setenv("PATH", str(executable.parent))
        settings = BaselineSettings(
            backends=Backends(antigravity_command=None),
            llm_credentials=LLMCredentials(gemini_api_key="key", google_api_key=""),
        )

        assert antigravity_command(settings) == command
        assert requirement_reason(Dep.ANTIGRAVITY, settings) is None

    def test_missing_executable_fails_requirement(self, tmp_path: Path) -> None:
        settings = BaselineSettings(
            backends=Backends(
                antigravity_command=(str(tmp_path / "missing-native-server"),)
            ),
            llm_credentials=LLMCredentials(gemini_api_key="key", google_api_key=""),
        )

        assert requirement_reason(Dep.ANTIGRAVITY, settings) is not None

    def test_vertex_only_auth_is_not_native_api_key_auth(
        self, native_executable: Path
    ) -> None:
        settings = BaselineSettings(
            backends=Backends(antigravity_command=(str(native_executable),)),
            llm_credentials=LLMCredentials(
                gemini_api_key="",
                google_api_key="",
                google_genai_use_vertexai="true",
                google_cloud_project="project",
            ),
        )

        assert requirement_reason(Dep.GOOGLE, settings) is None
        assert requirement_reason(Dep.ANTIGRAVITY, settings) is not None

    def test_registry_activates_the_google_native_requirement(self) -> None:
        spec = spec_for(Adapter.ANTIGRAVITY_ACP)

        assert spec.requires == (Dep.ANTIGRAVITY,)
        assert dep_lane(Dep.ANTIGRAVITY) == Lane.GOOGLE
        assert spec.e2e_pending is None
        assert tuple(
            lane.id for lane in ci_lanes() if Adapter.ANTIGRAVITY_ACP in lane.adapters
        ) == (Lane.GOOGLE,)
