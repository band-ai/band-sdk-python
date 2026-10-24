"""Narrow approval policy for the native OMP permission smoke."""

from __future__ import annotations

from band.adapters.omp import OMP_TOOL_APPROVAL_HEADER, OmpPermissionRequest
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.runtime.tools.types import BandTool

FORM_PROBE_TITLE = "Approve the E2E form probe?"
COMPLETION_TITLES = frozenset(
    f"{OMP_TOOL_APPROVAL_HEADER}{tool}"
    for tool in (BandTool.SEND_MESSAGE, BandTool.NO_REPLY)
)


async def deny_probe_allow_completion(
    request: OmpPermissionRequest,
    *,
    probe_decision: str | None,
) -> str | None:
    """Deny or cancel the probe while permitting only named Band completion."""
    if request.title == FORM_PROBE_TITLE:
        return probe_decision
    if request.title.partition("\n")[0] in COMPLETION_TITLES and request.options == (
        OMP_FORM_APPROVE,
        OMP_FORM_DENY,
    ):
        return OMP_FORM_APPROVE
    return None
