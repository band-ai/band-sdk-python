"""ModelFailuresReturnedAsData behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest
from google.adk.models.llm_response import LlmResponse

from band.core.exceptions import ProviderRunError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TurnResultAlreadyReported,
)
from band.core.turn import MISSING_REPLY
from band.testing import reported_failures
from tests.adapters.genaikit import (
    ModelFailureCases,
    PlatformSchemaFakeTools,
    ScriptedAdapter,
    no_candidates,
)
from tests.framework_conformance.turnprobes import turn_input


class TestModelFailuresReturnedAsData(ModelFailureCases):
    PROVIDER = "google_adk"

    @pytest.mark.asyncio
    async def test_no_candidates_preserves_native_error_or_reports_missing_reply(
        self, scripted_adapter: ScriptedAdapter
    ) -> None:
        response = no_candidates()
        native = LlmResponse.create(response)
        error, failures = {
            "UNKNOWN_ERROR": (
                ProviderRunError,
                [
                    {
                        "provider": self.PROVIDER,
                        "code": native.error_code,
                        "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
                        "detail": None,
                    }
                ],
            ),
            None: (
                TurnResultAlreadyReported,
                [
                    {
                        "provider": MISSING_REPLY.provider,
                        "code": MISSING_REPLY.code,
                        "message": MISSING_REPLY.message,
                        "detail": MISSING_REPLY.detail,
                    }
                ],
            ),
        }[native.error_code]
        adapter = await scripted_adapter(response)
        tools = PlatformSchemaFakeTools()
        with pytest.raises(error):
            await adapter.on_event(turn_input(tools))
        assert reported_failures(tools) == failures
