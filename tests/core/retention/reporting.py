"""A throwing platform failure-report boundary with a sensitive error payload."""

from __future__ import annotations

from band_sdk_core import AgentFailure

from band.testing import FakeAgentTools

SENSITIVE_RECOVERY_DETAIL = "private-room-identifier-and-secret"


class ThrowingFailureTools(FakeAgentTools):
    async def send_failure(self, failure: AgentFailure) -> dict[str, object]:
        raise RuntimeError(SENSITIVE_RECOVERY_DETAIL)
