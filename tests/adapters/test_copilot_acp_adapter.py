"""Tests for CopilotACPAdapter.

CopilotACPAdapter is a thin specialization of ACPClientAdapter — its contract is
how a CopilotACPAdapterConfig maps onto the base adapter's transport, auth, and
system-context wiring. Runtime/on_started behavior is covered by the generic ACP
client suite (tests/integrations/acp/), so these are construction-level tests.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from band.adapters.copilot_acp import (
    DEFAULT_COPILOT_COMMAND,
    CopilotACPAdapter,
    CopilotACPAdapterConfig,
)
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import NoopACPClientProfile


class TestCopilotACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(CopilotACPAdapter, ACPClientAdapter)

    def test_defaults_to_stdio_copilot_command(self) -> None:
        adapter = CopilotACPAdapter()
        assert adapter._command == list(DEFAULT_COPILOT_COMMAND)

    def test_no_config_equivalent_to_default_config(self) -> None:
        a, b = CopilotACPAdapter(), CopilotACPAdapter(CopilotACPAdapterConfig())
        for attr in ("_command", "_env", "_inject_band_tools"):
            assert getattr(a, attr) == getattr(b, attr)

    def test_custom_command_is_forwarded(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(command=("copilot", "--acp", "--yolo"))
        )
        assert adapter._command == ["copilot", "--acp", "--yolo"]

    def test_no_profile_uses_default_noop(self) -> None:
        # Copilot speaks vanilla ACP; the base adapter leaves profile unset and the
        # collecting client the runtime builds falls back to the no-op profile.
        adapter = CopilotACPAdapter()
        assert adapter._profile is None
        client = adapter._build_runtime()._client_factory()
        assert isinstance(client._profile, NoopACPClientProfile)

    def test_github_token_injected_into_stdio_env(self) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(github_token="ghp_x"))
        assert adapter._env == {"GITHUB_TOKEN": "ghp_x"}

    def test_no_env_without_token(self) -> None:
        # No token and no env → rely on the CLI's ambient login (stored / gh / BYOK).
        assert CopilotACPAdapter()._env is None

    def test_env_passthrough_for_any_auth_method(self) -> None:
        # A user can auth however Copilot supports — e.g. the highest-precedence
        # COPILOT_GITHUB_TOKEN, or BYOK provider keys — via the general env passthrough.
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(env={"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"})
        )
        assert adapter._env == {"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"}

    def test_github_token_convenience_merges_with_env(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(github_token="ghp_x", env={"GH_TOKEN": "gh"})
        )
        assert adapter._env == {"GH_TOKEN": "gh", "GITHUB_TOKEN": "ghp_x"}

    def test_explicit_env_github_token_wins_over_convenience(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(
                github_token="from-shortcut", env={"GITHUB_TOKEN": "from-env"}
            )
        )
        assert adapter._env == {"GITHUB_TOKEN": "from-env"}

    def test_custom_section_threaded_to_system_context(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(custom_section="You are a triage bot.")
        )
        assert adapter._custom_section == "You are a triage bot."

    def test_inject_band_tools_forwarded(self) -> None:
        assert CopilotACPAdapter()._inject_band_tools is True
        assert (
            CopilotACPAdapter(
                CopilotACPAdapterConfig(inject_band_tools=False)
            )._inject_band_tools
            is False
        )

    def test_additional_tools_forwarded(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        tool = (EchoInput, _echo)
        adapter = CopilotACPAdapter(additional_tools=[tool])
        assert adapter._custom_tools == [tool]


class TestCopilotACPAdapterTcpTransport:
    def test_tcp_config_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match="TCP ACP transport cannot guarantee room process isolation",
        ):
            CopilotACPAdapter(CopilotACPAdapterConfig(host="10.0.0.5", port=8080))

    def test_custom_command_with_tcp_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(
                    command=("copilot", "--acp", "--yolo"), host="10.0.0.5", port=8080
                )
            )

    def test_tcp_with_auth_warns_before_rejection(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # CopilotACPAdapter warns about ignored auth before the base adapter rejects TCP.
        with caplog.at_level(logging.WARNING, logger="band.adapters.copilot_acp"):
            with pytest.raises(
                ValueError,
                match="TCP ACP transport cannot guarantee room process isolation",
            ):
                CopilotACPAdapter(
                    CopilotACPAdapterConfig(
                        host="10.0.0.5", port=8080, github_token="ghp_x"
                    )
                )
        assert any("ignored over TCP" in r.message for r in caplog.records)
