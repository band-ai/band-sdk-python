"""OpenCode server adapter package.

Public import path is unchanged: ``from band.adapters.opencode import
OpencodeAdapter, OpencodeAdapterConfig``.
"""

from __future__ import annotations

from band.adapters.opencode.adapter import OpencodeAdapter
from band.adapters.opencode.config import (
    ApprovalMode,
    ApprovalReply,
    OpencodeAdapterConfig,
    QuestionMode,
)

__all__ = [
    "ApprovalMode",
    "ApprovalReply",
    "OpencodeAdapter",
    "OpencodeAdapterConfig",
    "QuestionMode",
]
