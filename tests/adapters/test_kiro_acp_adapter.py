"""Tests for KiroACPAdapter.

KiroACPAdapter is a thin specialization of ACPClientAdapter — its contract is
how a KiroACPAdapterConfig maps onto the base adapter's transport, auth, and
system-context wiring. Runtime/on_started behavior is covered by the generic ACP
client suite (tests/integrations/acp/), so these are construction-level tests.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from band.adapters.kiro_acp import (
    DEFAULT_KIRO_COMMAND,
    KiroACPAdapter,
    KiroACPAdapterConfig,
)
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import KiroACPClientProfile
from band.integrations.acp.session_config import ACPConfigRequest
from band.workspaces import create_room_workspace_resolver


class TestKiroACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(KiroACPAdapter, ACPClientAdapter)

    def test_defaults_to_stdio_kiro_command(self) -> None:
        adapter = KiroACPAdapter()
        assert adapter._command == list(DEFAULT_KIRO_COMMAND)

    def test_no_config_equivalent_to_default_config(self) -> None:
        a, b = KiroACPAdapter(), KiroACPAdapter(KiroACPAdapterConfig())
        for attr in ("_command", "_env", "_inject_band_tools"):
            assert getattr(a, attr) == getattr(b, attr)

    def test_custom_command_is_forwarded(self) -> None:
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(command=("kiro-cli", "acp", "--agent-engine", "v3"))
        )
        assert adapter._command == ["kiro-cli", "acp", "--agent-engine", "v3"]

    def test_uses_kiro_profile(self) -> None:
        # Unlike Copilot (vanilla ACP, no profile), Kiro emits `_kiro.dev/*`
        # extensions -- both the adapter and the runtime it builds must carry
        # the profile that understands them.
        adapter = KiroACPAdapter()
        assert isinstance(adapter._profile, KiroACPClientProfile)
        client = adapter._build_runtime()._client_factory()
        assert isinstance(client._profile, KiroACPClientProfile)

    def test_no_env_by_default(self) -> None:
        # No env → rely on the CLI's ambient login (`kiro-cli login`).
        assert KiroACPAdapter()._env is None

    def test_env_passthrough_for_any_auth_method(self) -> None:
        # Kiro has no BYOK-convenience field (unlike Copilot's github_token):
        # KIRO_API_KEY, KIRO_HOME, or anything else rides the general passthrough.
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(
                env={"KIRO_API_KEY": "tok", "KIRO_HOME": "/tmp/kiro-home"}
            )
        )
        assert adapter._env == {"KIRO_API_KEY": "tok", "KIRO_HOME": "/tmp/kiro-home"}

    def test_workspace_for_room_forwarded(self, tmp_path: Path) -> None:
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(
                workspace_for_room=create_room_workspace_resolver(tmp_path)
            )
        )
        assert adapter._workspace("room-a") == str(tmp_path / "room-a")

    def test_custom_section_threaded_to_system_context(self) -> None:
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(custom_section="You are a triage bot.")
        )
        assert adapter._custom_section == "You are a triage bot."

    def test_inject_band_tools_forwarded(self) -> None:
        assert KiroACPAdapter()._inject_band_tools is True
        assert (
            KiroACPAdapter(
                KiroACPAdapterConfig(inject_band_tools=False)
            )._inject_band_tools
            is False
        )

    def test_additional_tools_forwarded(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        tool = (EchoInput, _echo)
        adapter = KiroACPAdapter(additional_tools=[tool])
        assert adapter._custom_tools == [tool]

    def test_session_config_resolver_is_forwarded(self) -> None:
        async def resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"model": "kiro-default"}

        adapter = KiroACPAdapter(KiroACPAdapterConfig(resolve_session_config=resolver))

        assert adapter._resolve_session_config is resolver
