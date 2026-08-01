"""Delivery evidence: receipt model and derivation from tool/ACP outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator
from band.core.bases import FrozenModel

from band.core.contracts.events import ToolStatus

if TYPE_CHECKING:
    from band.runtime.tools import ToolCallOutcome


class DeliveryReceipt(FrozenModel):
    """Evidence of a successful room-posting tool call.

    ``tool_name`` must satisfy ``is_room_posting_tool()``. Failed posts never
    produce a receipt.
    """

    tool_name: str = Field(min_length=1)

    @field_validator("tool_name")
    @classmethod
    def require_room_posting_tool(cls, value: str) -> str:
        from band.runtime.tools import is_room_posting_tool

        if not is_room_posting_tool(value):
            raise ValueError(f"{value!r} is not a room-posting tool")
        return value


def receipt_from_tool_outcome(
    tool_name: str,
    outcome: ToolCallOutcome,
) -> DeliveryReceipt | None:
    """Mint a receipt when ``outcome.ok`` on a room-posting tool."""
    from band.runtime.tools import is_room_posting_tool

    if not outcome.ok or not is_room_posting_tool(tool_name):
        return None
    return DeliveryReceipt(tool_name=tool_name)


def receipt_from_acp_chunks(
    chunks: Sequence[Any],
) -> DeliveryReceipt | None:
    """Mint a receipt from a room post visible only in the ACP chunk stream."""
    from band.integrations.acp.types import ChunkType
    from band.runtime.tools import is_room_posting_tool

    posting_call_ids: dict[str, str] = {}
    for chunk in chunks:
        metadata = getattr(chunk, "metadata", None) or {}
        call_id = str(metadata.get("tool_call_id", ""))
        content = getattr(chunk, "content", "")
        status = metadata.get("status")

        match getattr(chunk, "chunk_type", None):
            case ChunkType.TOOL_CALL if is_room_posting_tool(content):
                if status == ToolStatus.COMPLETED:
                    return DeliveryReceipt(tool_name=content)
                if call_id:
                    posting_call_ids[call_id] = content
            case ChunkType.TOOL_RESULT if (
                call_id in posting_call_ids and status == ToolStatus.COMPLETED
            ):
                return DeliveryReceipt(tool_name=posting_call_ids[call_id])
            case _:
                continue
    return None
