"""Codex approval wire types."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

ApprovalDecision = Literal["accept", "acceptForSession", "decline"]


def approval_response(decision: ApprovalDecision) -> dict[str, Any]:
    return {"decision": decision}


@dataclass
class PendingApproval:
    request_id: int | str
    method: str
    summary: str
    created_at: datetime
    future: asyncio.Future[ApprovalDecision]
    session_key: str = ""
    native_key: str = ""
