"""Permission policy for the trusted OMP native-form smoke fixture."""

from __future__ import annotations

from band.integrations.acp.client_adapter import ACPPermissionRequest
from band.integrations.acp.client_runtime import option_id_of_kind
from band.integrations.omp import (
    OMP_APPROVAL_FORM_TOOL_NAME,
    normalize_omp_mcp_tool_name,
)
from band.runtime.tools.types import BandTool

# OMP 18.2.8's approval.ts puts this header on the first line only.
OMP_TOOL_APPROVAL_HEADER = "Allow tool: "
COMPLETION_TOOLS = frozenset({BandTool.SEND_MESSAGE, BandTool.NO_REPLY})


async def deny_probe_allow_completion(request: ACPPermissionRequest) -> str | None:
    """Deny the probe and unrelated asks; grant completion only once."""
    call = request.tool_call
    if call.name != OMP_APPROVAL_FORM_TOOL_NAME or not isinstance(call.arguments, dict):
        return None
    message = call.arguments.get("message")
    if not isinstance(message, str):
        return None
    first_line = message.partition("\n")[0]
    if not first_line.startswith(OMP_TOOL_APPROVAL_HEADER):
        return None
    name = first_line.removeprefix(OMP_TOOL_APPROVAL_HEADER)
    if normalize_omp_mcp_tool_name(name, COMPLETION_TOOLS) is None:
        return None
    return option_id_of_kind(request.options, "allow_once")
