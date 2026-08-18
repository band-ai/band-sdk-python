"""
Band Platform Layer - Wire-level connection to Band platform.

Components:
    BandLink: WebSocket connection + event dispatch (REST via .rest)
    PlatformEvent: Single event type for all platform events
    CredentialResolver: Delegated-credential exchange for cross-owner
        messages (INT-993), with its typed DelegationError taxonomy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.platform.delegation_exchange import (
        AudienceNotAllowed as AudienceNotAllowed,
        ConsentMissing as ConsentMissing,
        CredentialResolver as CredentialResolver,
        CrossOrgBlocked as CrossOrgBlocked,
        DelegatedToken as DelegatedToken,
        DelegationDenied as DelegationDenied,
        DelegationError as DelegationError,
        DelegationUnavailable as DelegationUnavailable,
        ExchangeRateLimited as ExchangeRateLimited,
        NoDelegation as NoDelegation,
        OnBehalfOf as OnBehalfOf,
        ProviderNotConnected as ProviderNotConnected,
        Revoked as Revoked,
        WindowExpired as WindowExpired,
    )
    from band.platform.event import PlatformEvent as PlatformEvent
    from band.platform.link import BandLink as BandLink

__all__, __getattr__ = lazy_exports(
    __name__,
    event=["PlatformEvent"],
    link=["BandLink"],
    delegation_exchange=[
        "AudienceNotAllowed",
        "ConsentMissing",
        "CredentialResolver",
        "CrossOrgBlocked",
        "DelegatedToken",
        "DelegationDenied",
        "DelegationError",
        "DelegationUnavailable",
        "ExchangeRateLimited",
        "NoDelegation",
        "OnBehalfOf",
        "ProviderNotConnected",
        "Revoked",
        "WindowExpired",
    ],
)
