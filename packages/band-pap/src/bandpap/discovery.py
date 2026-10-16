"""Parse discovery and verify domain/issuer binding before using endpoints."""

from __future__ import annotations

import re
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def validate_https_url(value: str) -> str:
    """Keep the exact URL spelling, especially for issuer equality checks."""
    if any(character.isspace() for character in value) or "\\" in value:
        raise ValueError("HTTPS URLs cannot contain whitespace or backslashes")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise ValueError("Invalid HTTPS URL") from error
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or port == 0
    ):
        raise ValueError(
            "Expected an absolute HTTPS URL without credentials or fragment"
        )
    return value


def validate_domain(value: str) -> str:
    if len(value) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("Expected a DNS host without port or URL components")
    return value


def validate_issuer(value: str) -> str:
    if urlsplit(value).query:
        raise ValueError("OAuth issuer cannot have a query")
    return value


HttpsUrl = Annotated[str, AfterValidator(validate_https_url)]
Domain = Annotated[str, AfterValidator(validate_domain)]
Issuer = Annotated[HttpsUrl, AfterValidator(validate_issuer)]


class DiscoveryModel(BaseModel):
    """Unknown fields are ignored for draft forward compatibility."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Organization(DiscoveryModel):
    name: str = Field(min_length=1)
    domain: Domain


class AuthDiscovery(DiscoveryModel):
    issuer: Issuer


class ConversationEndpoint(DiscoveryModel):
    type: str = Field(min_length=1)
    endpoint: HttpsUrl
    resource: HttpsUrl | None = None


class AgentDiscovery(DiscoveryModel):
    protocols: tuple[ConversationEndpoint, ...] = Field(min_length=1)


class ApiDiscovery(DiscoveryModel):
    type: str = Field(min_length=1)
    url: HttpsUrl
    description: str
    resource: HttpsUrl | None = None


class WebDiscovery(DiscoveryModel):
    browser_session_endpoint: HttpsUrl | None = None


class DiscoveryDocument(DiscoveryModel):
    protocol_version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    organization: Organization
    auth: AuthDiscovery | None = None
    agent: AgentDiscovery | None = None
    apis: tuple[ApiDiscovery, ...] = ()
    web: WebDiscovery | None = None

    @model_validator(mode="after")
    def validate_interfaces(self) -> Self:
        major, _minor = self.protocol_version.split(".")
        if int(major) != 0:
            raise ValueError("Unsupported PAP protocol major version")
        if self.agent is None and not self.apis and self.web is None:
            raise ValueError("Discovery requires an agent, API, or website")
        needs_session = (
            self.agent is not None
            or bool(self.apis)
            or (self.web is not None and self.web.browser_session_endpoint is not None)
        )
        if needs_session and self.auth is None:
            raise ValueError("Session interfaces require an OAuth issuer")
        return self


class OAuthMetadata(DiscoveryModel):
    issuer: Issuer
    token_endpoint: HttpsUrl
    revocation_endpoint: HttpsUrl
    poppy_domains: tuple[Domain, ...] = Field(min_length=1)
    dpop_signing_alg_values_supported: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_algorithms(self) -> Self:
        if "ES256" not in self.dpop_signing_alg_values_supported:
            raise ValueError("PAP requires ES256 proof support")
        return self


def verify_discovery_binding(
    document: DiscoveryDocument,
    *,
    fetched_url: str,
    metadata: OAuthMetadata | None = None,
) -> None:
    """Check documents already obtained by a trusted metadata fetcher.

    This does not fetch, validate DNS destinations, authorize requests, or
    verify credentials. ``fetched_url`` must be the observed final HTTPS URL.
    """
    validate_https_url(fetched_url)
    host = urlsplit(fetched_url).hostname
    domain = document.organization.domain
    if host is None or host.lower() != domain.lower():
        raise ValueError("Organization domain does not match discovery response host")
    if document.auth is None:
        if metadata is not None:
            raise ValueError("OAuth metadata supplied for a website without an issuer")
        return
    if metadata is None:
        raise ValueError("OAuth metadata is required to verify the company")
    if metadata.issuer != document.auth.issuer:
        raise ValueError("OAuth issuer must exactly match discovery")
    if domain.lower() not in {item.lower() for item in metadata.poppy_domains}:
        raise ValueError("OAuth issuer does not vouch for the discovery domain")
