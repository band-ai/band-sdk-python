"""Construction tests for the thin OMP ACP adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from acp.schema import (
    AcceptElicitationResponse,
    DeclineElicitationResponse,
    ElicitationFormSessionMode,
)
from pydantic import BaseModel
from unittest.mock import AsyncMock, MagicMock

from band.adapters.omp_acp import (
    DEFAULT_OMP_ACP_COMMAND,
    OmpACPAdapter,
    OmpACPAdapterConfig,
)
from band.integrations.acp.client_adapter import ACPClientAdapter, ACPPermissionRequest
from band.integrations.acp.session_config import ACPConfigRequest
from band.integrations.omp import (
    OMP_ALWAYS_ASK_APPROVAL_MODE,
    OMP_ACP_SUBCOMMAND,
    OMP_APPROVAL_MODE_ARGUMENT,
    OMP_APPROVAL_MODE_ASSIGNMENT_PREFIX,
    OMP_AUTO_APPROVE_ARGUMENT,
    OMP_AUTO_APPROVAL_MODES,
    OMP_BINARY,
    OMP_ELICITATION_APPROVE_OPTION,
    OMP_ELICITATION_CALL_ID_PREFIX,
    OMP_ELICITATION_DENY_OPTION,
    OMP_ELICITATION_FIELD,
    OMP_ELICITATION_MESSAGE_FIELD,
    OMP_GEMINI_API_KEY_ENV,
    OMP_MCP_CONTENT_FIELD,
    OMP_MCP_DEVICE_PREFIX,
    OMP_MCP_PATH_FIELD,
    OMP_OPENAI_API_KEY_ENV,
    OMP_STATE_DIRECTORY_ENV,
    OMP_YOLO_ARGUMENT,
)
from band.runtime.tools import BandTool
from tests.e2e.baseline.settings import (
    DEFAULT_OMP_MODEL,
    Backends,
    BaselineSettings,
    LLMCredentials,
)
from tests.e2e.baseline.toolkit.builders import omp_acp_env


class TestOmpACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(OmpACPAdapter, ACPClientAdapter)

    def test_defaults_to_omp_acp_stdio_command(self) -> None:
        adapter = OmpACPAdapter()

        assert adapter._command == list(DEFAULT_OMP_ACP_COMMAND)
        assert adapter._command[-2:] == [
            OMP_APPROVAL_MODE_ARGUMENT,
            OMP_ALWAYS_ASK_APPROVAL_MODE,
        ]
        assert adapter._host is None
        assert adapter._port is None
        assert adapter._runtime._use_unstable_protocol
        assert adapter._runtime._client_capabilities is not None
        assert adapter._runtime._client_capabilities.elicitation is not None
        assert adapter._runtime._client_capabilities.elicitation.form is not None

    def test_config_forwards_shared_acp_options(self) -> None:
        async def session_resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"model": "google/gemini-2.5-flash"}

        async def permission_resolver(request: ACPPermissionRequest) -> str | None:
            del request
            return None

        adapter = OmpACPAdapter(
            OmpACPAdapterConfig(
                command=(
                    "custom-omp",
                    OMP_ACP_SUBCOMMAND,
                    OMP_APPROVAL_MODE_ARGUMENT,
                    OMP_ALWAYS_ASK_APPROVAL_MODE,
                ),
                cwd="/tmp/omp",
                env={OMP_GEMINI_API_KEY_ENV: "key"},
                custom_section="Use concise replies.",
                inject_band_tools=False,
                mcp_servers=[{"name": "existing"}],
                resolve_session_config=session_resolver,
                resolve_permission=permission_resolver,
            )
        )

        assert adapter._command == [
            "custom-omp",
            OMP_ACP_SUBCOMMAND,
            OMP_APPROVAL_MODE_ARGUMENT,
            OMP_ALWAYS_ASK_APPROVAL_MODE,
        ]
        assert adapter._cwd == "/tmp/omp"
        assert adapter._env == {OMP_GEMINI_API_KEY_ENV: "key"}
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
        ("model", "credentials", "expected_env"),
        [
            (
                DEFAULT_OMP_MODEL,
                LLMCredentials(openai_api_key="openai-key"),
                {OMP_OPENAI_API_KEY_ENV: "openai-key"},
            ),
            (
                "google/gemini-2.5-flash",
                LLMCredentials(gemini_api_key="gemini-key"),
                {OMP_GEMINI_API_KEY_ENV: "gemini-key"},
            ),
        ],
    )
    def test_e2e_spawn_uses_the_selected_model_provider_key(
        self,
        model: str,
        credentials: LLMCredentials,
        expected_env: dict[str, str],
    ) -> None:
        settings = BaselineSettings(
            llm_credentials=credentials,
            backends=Backends(omp_model=model),
        )

        env = omp_acp_env(settings, "/tmp/omp-state")

        assert env == {**expected_env, OMP_STATE_DIRECTORY_ENV: "/tmp/omp-state"}

    @pytest.mark.parametrize(
        "command",
        [
            (OMP_BINARY, OMP_ACP_SUBCOMMAND, OMP_YOLO_ARGUMENT),
            (OMP_BINARY, OMP_ACP_SUBCOMMAND, OMP_AUTO_APPROVE_ARGUMENT),
            *[
                (OMP_BINARY, OMP_ACP_SUBCOMMAND, OMP_APPROVAL_MODE_ARGUMENT, mode)
                for mode in OMP_AUTO_APPROVAL_MODES
            ],
            *[
                (
                    OMP_BINARY,
                    OMP_ACP_SUBCOMMAND,
                    f"{OMP_APPROVAL_MODE_ASSIGNMENT_PREFIX}{mode}",
                )
                for mode in OMP_AUTO_APPROVAL_MODES
            ],
        ],
    )
    def test_rejects_auto_approval_modes(self, command: tuple[str, ...]) -> None:
        with pytest.raises(ValueError, match="bypass Band permission resolution"):
            OmpACPAdapter(OmpACPAdapterConfig(command=command))

    def test_decodes_its_band_mcp_device_call(self) -> None:
        adapter = OmpACPAdapter()
        participant_id = "agent-123"
        raw_input = {
            OMP_MCP_PATH_FIELD: (f"{OMP_MCP_DEVICE_PREFIX}{BandTool.ADD_PARTICIPANT}"),
            OMP_MCP_CONTENT_FIELD: json.dumps({"participant_ids": [participant_id]}),
        }

        normalized = adapter._normalize_acp_tool_call(
            SimpleNamespace(raw_input=raw_input)
        )

        assert normalized == (
            BandTool.ADD_PARTICIPANT,
            {"participant_ids": [participant_id]},
        )

    def test_leaves_a_foreign_mcp_device_call_unattributed(self) -> None:
        adapter = OmpACPAdapter()
        raw_input = {
            OMP_MCP_PATH_FIELD: f"{OMP_MCP_DEVICE_PREFIX}other_{BandTool.ADD_PARTICIPANT}",
            OMP_MCP_CONTENT_FIELD: json.dumps({}),
        }

        assert (
            adapter._normalize_acp_tool_call(SimpleNamespace(raw_input=raw_input))
            is None
        )

    @pytest.mark.asyncio
    async def test_approval_elicitation_uses_the_shared_permission_resolver(
        self,
    ) -> None:
        observed_requests: list[ACPPermissionRequest] = []

        async def approve(request: ACPPermissionRequest) -> str | None:
            observed_requests.append(request)
            return OMP_ELICITATION_APPROVE_OPTION

        adapter = OmpACPAdapter(OmpACPAdapterConfig(resolve_permission=approve))
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        message = "Allow tool: write\nPath: /tmp/guarded.txt"
        response = await adapter._make_elicitation_handler(emitter, "room-1")(
            message=message,
            mode=_omp_approval_form("session-1"),
        )

        assert isinstance(response, AcceptElicitationResponse)
        assert response.content == {
            OMP_ELICITATION_FIELD: OMP_ELICITATION_APPROVE_OPTION
        }
        assert observed_requests[0].room_id == "room-1"
        assert observed_requests[0].tool_call.arguments == {
            OMP_ELICITATION_MESSAGE_FIELD: message
        }
        emitter.open_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_denied_approvals_in_one_session_have_distinct_call_ids(self) -> None:
        async def deny(request: ACPPermissionRequest) -> str | None:
            del request
            return OMP_ELICITATION_DENY_OPTION

        adapter = OmpACPAdapter(OmpACPAdapterConfig(resolve_permission=deny))
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        handler = adapter._make_elicitation_handler(emitter, "room-1")

        for message in ("Allow tool: write", "Allow tool: bash"):
            response = await handler(
                message=message,
                mode=_omp_approval_form("session-1"),
            )
            assert isinstance(response, DeclineElicitationResponse)

        denied_calls = [
            call.kwargs["call"] for call in emitter.open_permission.await_args_list
        ]
        denied_call_ids = {call.tool_call_id for call in denied_calls}
        assert len(denied_call_ids) == 2
        assert all(
            call_id.startswith(f"{OMP_ELICITATION_CALL_ID_PREFIX}:session-1:")
            for call_id in denied_call_ids
        )

    @pytest.mark.asyncio
    async def test_unknown_elicitation_form_is_declined(self) -> None:
        adapter = OmpACPAdapter()
        emitter = MagicMock()
        emitter.open_permission = AsyncMock()
        response = await adapter._make_elicitation_handler(emitter, "room-1")(
            message="Choose a model",
            mode=ElicitationFormSessionMode(
                session_id="session-1",
                requested_schema={
                    "type": "object",
                    "properties": {
                        OMP_ELICITATION_FIELD: {
                            "type": "string",
                            "enum": [OMP_ELICITATION_APPROVE_OPTION],
                        }
                    },
                },
            ),
        )

        assert isinstance(response, DeclineElicitationResponse)
        emitter.open_permission.assert_not_awaited()


def _omp_approval_form(session_id: str) -> ElicitationFormSessionMode:
    """The OMP approval form contract exercised by the adapter."""
    return ElicitationFormSessionMode(
        session_id=session_id,
        requested_schema={
            "type": "object",
            "properties": {
                OMP_ELICITATION_FIELD: {
                    "type": "string",
                    "enum": [
                        OMP_ELICITATION_APPROVE_OPTION,
                        OMP_ELICITATION_DENY_OPTION,
                    ],
                }
            },
            "required": [OMP_ELICITATION_FIELD],
        },
    )
