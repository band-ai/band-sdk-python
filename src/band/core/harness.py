"""Host-facing probe results shared by the coding-agent adapters.

``preflight()`` and ``list_models()`` answer questions about a harness before
any room starts: can it be launched and does it answer, and which models does
it offer this account. Each adapter parses its own harness payload into these
types, so the public vocabulary lives only here.
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


class HarnessModel(BaseModel):
    """One model a harness offers, as reported by the harness itself.

    ``id`` is the value the adapter's own ``model`` setting accepts.
    ``efforts`` lists the reasoning-effort values the harness accepts for
    it (empty when it has none), and ``default_effort`` the one it uses when
    none is given, if the harness reports it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    provider: str | None = None
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    is_default: bool = False


__all__ = ["HarnessModel", "PreflightResult"]
