"""Antigravity's pending builder uses API keys and owns disposable native state."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from band.core.types import AdapterFeatures, Capability
from band.workspaces import create_room_workspace_resolver
from tests.adaptercleanup import HostKind, host_for
from tests.e2e.baseline.settings import BaselineSettings, LLMCredentials
from tests.e2e.baseline.toolkit.adapters import Adapter, build_adapter

if TYPE_CHECKING:
    from band.adapters.antigravity_acp import AntigravityACPAdapter

pytest.importorskip("acp")


class TestAntigravityBuilder:
    @pytest.mark.parametrize(
        ("gemini_key", "google_key", "expected_key"),
        [
            ("gemini-key", "google-key", "gemini-key"),
            ("gemini-key", "", "gemini-key"),
            ("", "google-key", "google-key"),
        ],
    )
    async def test_api_key_configuration_and_native_state_are_owned_until_host_stop(
        self, gemini_key: str, google_key: str, expected_key: str
    ) -> None:
        settings = BaselineSettings(
            llm_credentials=LLMCredentials(
                gemini_api_key=gemini_key, google_api_key=google_key
            )
        )
        adapter = cast(
            "AntigravityACPAdapter",
            build_adapter(
                Adapter.ANTIGRAVITY_ACP,
                settings,
                prompt="Use the room tools",
                features=AdapterFeatures(capabilities={Capability.MEMORY}),
            ),
        )
        sandbox = Path(adapter.config.gemini_home).parent
        async with host_for(adapter, HostKind.AGENT) as host:
            await host.start()
            assert adapter.config.gemini_api_key == expected_key
            assert adapter.config.model == settings.llm_models.gemini_model
            assert adapter.config.custom_section == "Use the room tools"
            assert adapter.config.approval_mode == "auto_decline"
            assert adapter.features.capabilities == frozenset({Capability.MEMORY})
            assert adapter.config.cwd == str(sandbox)
            assert sandbox.is_dir()
        assert not sandbox.exists()

    async def test_room_workspace_resolver_preserves_disposable_native_home(
        self, tmp_path: Path
    ) -> None:
        settings = BaselineSettings(
            llm_credentials=LLMCredentials(
                gemini_api_key="gemini-key", google_api_key=""
            )
        )
        adapter = cast(
            "AntigravityACPAdapter",
            build_adapter(
                Adapter.ANTIGRAVITY_ACP,
                settings,
                workspace_for_room=create_room_workspace_resolver(tmp_path),
            ),
        )
        sandbox = Path(adapter.config.gemini_home).parent
        async with host_for(adapter, HostKind.AGENT) as host:
            await host.start()
            assert adapter.config.cwd is None
            assert sandbox.is_dir()
        assert not sandbox.exists()
        assert tmp_path.is_dir()

    def test_api_key_authentication_rejects_missing_keys(self) -> None:
        settings = BaselineSettings(
            llm_credentials=LLMCredentials(gemini_api_key="", google_api_key="")
        )
        with pytest.raises(ValueError, match="GEMINI_API_KEY or GOOGLE_API_KEY"):
            build_adapter(Adapter.ANTIGRAVITY_ACP, settings)
