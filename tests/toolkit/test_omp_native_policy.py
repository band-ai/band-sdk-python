"""Keep the live native permission probe from granting unrelated tools."""

from __future__ import annotations

import pytest

from band.adapters.omp import OMP_TOOL_APPROVAL_HEADER, OmpPermissionRequest
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from tests.e2e.baseline.smoke.samples.omppermission import (
    FORM_PROBE_TITLE,
    deny_probe_allow_completion,
)


class TestNativeProbePolicy:
    @pytest.mark.parametrize(
        ("title", "options", "expected"),
        [
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_message\nArguments: {{}}",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                OMP_FORM_APPROVE,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_no_reply\nArguments: {{}}",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                OMP_FORM_APPROVE,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}write\nArguments: band_send_message",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}eval",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_event",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}mcp__other_band_send_message",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_message suffix",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"Description\n{OMP_TOOL_APPROVAL_HEADER}band_send_message",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_message\r\nArguments: {{}}",
                (OMP_FORM_APPROVE, OMP_FORM_DENY),
                None,
            ),
            (
                f"{OMP_TOOL_APPROVAL_HEADER}band_send_message",
                ("Always", OMP_FORM_DENY),
                None,
            ),
        ],
    )
    async def test_grants_only_named_completion(
        self,
        title: str,
        options: tuple[str, ...],
        expected: str | None,
    ) -> None:
        request = OmpPermissionRequest(
            room_id="room",
            session_id="session",
            title=title,
            options=options,
        )
        assert (
            await deny_probe_allow_completion(request, probe_decision=OMP_FORM_DENY)
            == expected
        )

    @pytest.mark.parametrize("decision", [OMP_FORM_DENY, None])
    async def test_preserves_the_probe_denial_or_cancellation(
        self, decision: str | None
    ) -> None:
        probe = OmpPermissionRequest(
            room_id="room",
            session_id="session",
            title=FORM_PROBE_TITLE,
            options=(OMP_FORM_APPROVE, OMP_FORM_DENY),
        )
        assert (
            await deny_probe_allow_completion(probe, probe_decision=decision)
            == decision
        )
