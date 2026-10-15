"""Narrow approval policy for the native OMP permission smoke."""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.runtime.tools.types import BandTool

if TYPE_CHECKING:
    from band.adapters.omp import OmpPermissionRequest

FORM_PROBE_TITLE = "Approve the E2E form probe?"


async def deny_probe_allow_completion(
    request: OmpPermissionRequest,
    *,
    probe_decision: str | None,
) -> str | None:
    """Deny or cancel the probe while permitting only named Band completion."""
    if request.title == FORM_PROBE_TITLE:
        return probe_decision
    if request.title.partition("\n")[0] in {
        f"Allow tool: {BandTool.SEND_MESSAGE}",
        f"Allow tool: {BandTool.NO_REPLY}",
    } and request.options == ("Approve", "Deny"):
        return "Approve"
    return None
