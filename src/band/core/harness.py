"""Host-facing probe results shared by the coding-agent adapters.

``preflight()`` answers whether a harness can be launched and answers its
handshake, before any room starts. Each adapter reports that answer as a
``PreflightResult``, so the public vocabulary lives only here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PreflightResult(BaseModel):
    """Whether an adapter's harness can be launched and answers its handshake.

    ``reason`` says what failed and ``remedy`` what a person can do about
    it; both are ``None`` on success.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    reason: str | None = None
    remedy: str | None = None

    @classmethod
    def passed(cls) -> PreflightResult:
        return cls(ok=True)

    @classmethod
    def failed(cls, reason: str, remedy: str) -> PreflightResult:
        return cls(ok=False, reason=reason, remedy=remedy)


__all__ = ["PreflightResult"]
