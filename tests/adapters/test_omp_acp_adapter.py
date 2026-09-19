"""Construction tests for the thin OMP ACP adapter."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from band.adapters.omp_acp import (
    DEFAULT_OMP_ACP_COMMAND,
    OmpACPAdapter,
    OmpACPAdapterConfig,
)
from band.integrations.acp.client_adapter import ACPClientAdapter, ACPPermissionRequest
from band.integrations.acp.session_config import ACPConfigRequest


class TestOmpACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(OmpACPAdapter, ACPClientAdapter)

    def test_defaults_to_omp_acp_stdio_command(self) -> None:
        adapter = OmpACPAdapter()

        assert adapter._command == list(DEFAULT_OMP_ACP_COMMAND)
        assert adapter._host is None
        assert adapter._port is None

    def test_config_forwards_shared_acp_options(self) -> None:
        async def session_resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"model": "google/gemini-2.5-flash"}

        async def permission_resolver(request: ACPPermissionRequest) -> str | None:
            del request
            return None

        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(
                command=("custom-omp", "acp", "--approval-mode", "always-ask"),
                cwd="/tmp/omp",
                env={"GEMINI_API_KEY": "key"},
                custom_section="Use concise replies.",
                inject_band_tools=False,
                mcp_servers=[{"name": "existing"}],
                resolve_session_config=session_resolver,
                resolve_permission=permission_resolver,
            )
        )

        assert adapter._command == [
            "custom-omp",
            "acp",
            "--approval-mode",
            "always-ask",
        ]
        assert adapter._cwd == "/tmp/omp"
        assert adapter._env == {"GEMINI_API_KEY": "key"}
        assert adapter._custom_section == "Use concise replies."
        assert adapter._inject_band_tools is False
        assert adapter._mcp_servers == [{"name": "existing"}]
        assert adapter._resolve_session_config is session_resolver
        assert adapter._resolve_permission is permission_resolver

    def test_additional_tools_and_features_are_forwarded(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def echo(text: str) -> str:
            return text

        tool = (EchoInput, echo)
        adapter = OmpACPAdapter(additional_tools=[tool])

        assert adapter._custom_tools == [tool]

    @pytest.mark.parametrize(
        "command",
        [
            ("omp", "acp", "--yolo"),
            ("omp", "acp", "--auto-approve"),
            ("omp", "acp", "--approval-mode", "yolo"),
        ],
    )
    def test_rejects_auto_approval_modes(self, command: tuple[str, ...]) -> None:
        with pytest.raises(ValueError, match="bypass Band permission resolution"):
            OmpACPAdapter(OmpACPAdapterConfig(command=command))
