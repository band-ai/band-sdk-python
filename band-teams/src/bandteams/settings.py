"""Validate the operator-owned hosted qualification configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

HOSTED_REST = "https://app.band.ai"
HOSTED_WS = "wss://app.band.ai/api/v1/socket/websocket"
HOSTED_AUTH = "https://auth.band.ai"
REQUEST_SECONDS = 30
CONFIRM_SECONDS = 600
REVIEW_BYTES = 80_000
SCOPES = "openid email profile offline_access"


class StrictModel(BaseModel):
    """Reject accidental configuration and correlation fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Room(StrictModel):
    """Name one supplied synthetic room and eligible targets."""

    id: str = Field(min_length=1)
    recipients: tuple[str, ...] = Field(min_length=1)


class Binding(StrictModel):
    """Allow one human identity and one private destination."""

    alias: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")
    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    band_user_id: str = Field(min_length=1)
    team_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    rooms: tuple[Room, Room]
    private_owner_verified: bool
    rsc_consent_verified: bool

    @model_validator(mode="after")
    def unique_rooms(self) -> Self:
        if len({room.id for room in self.rooms}) != 2:
            raise ValueError("Supply two distinct rooms per person")
        if any(self.band_user_id in room.recipients for room in self.rooms):
            raise ValueError("Self recipients are forbidden")
        if any(
            len(set(room.recipients)) != len(room.recipients) for room in self.rooms
        ):
            raise ValueError("Duplicate recipients are forbidden")
        return self


class Manifest(StrictModel):
    """Declare the allowed enterprise identities and isolated destinations."""

    people: tuple[Binding, ...] = Field(min_length=2)
    ingress_url: str
    deployment: str = Field(pattern=r"^(local-tunnel|stable-server)$")
    tunnel_expires_at: float | None = None
    installed_manifest: Path
    entitlement_evidence: Path
    teams_client_version: str = Field(min_length=1)
    posts_layout_verified: bool

    @model_validator(mode="after")
    def isolated_people(self) -> Self:
        for field in (
            "alias",
            "actor_id",
            "band_user_id",
            "team_id",
            "channel_id",
            "group_id",
        ):
            if len({getattr(person, field) for person in self.people}) != len(
                self.people
            ):
                raise ValueError(
                    "People must have separate identities and destinations"
                )
        if len({person.tenant_id for person in self.people}) != 1:
            raise ValueError("All employees must belong to the same tenant")
        ingress = urlsplit(self.ingress_url)
        if (
            ingress.scheme != "https"
            or not ingress.hostname
            or ingress.path != "/api/messages"
        ):
            raise ValueError("A public HTTPS /api/messages tunnel is required")
        if ingress.username or ingress.password or ingress.query or ingress.fragment:
            raise ValueError("Ingress must not contain credentials or query parameters")
        return self

    def person(self, tenant: str, actor: str) -> Binding:
        for person in self.people:
            if (person.tenant_id, person.actor_id) == (tenant, actor):
                return person
        raise ValueError("Identity is outside the run manifest")


class BandSettings(BaseSettings):
    """Read Band values exclusively from the repository .env.test file."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    BAND_REST_URL: str = HOSTED_REST
    BAND_WS_URL: str = HOSTED_WS
    BAND_OAUTH_CLIENT_ID: str = Field(min_length=1)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, dotenv_settings)

    @model_validator(mode="after")
    def hosted_only(self) -> Self:
        if self.BAND_REST_URL != HOSTED_REST or self.BAND_WS_URL != HOSTED_WS:
            raise ValueError(
                "BLOCKED: qualification requires the approved hosted Band URLs"
            )
        return self


class TeamsSettings(BaseSettings):
    """Read Microsoft credentials separately from Band configuration."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    CLIENT_ID: str = Field(min_length=1)
    CLIENT_SECRET: SecretStr
    TENANT_ID: str = Field(min_length=1)
    PORT: int = Field(default=3978, ge=1, le=65535)


def load_manifest(path: Path) -> Manifest:
    """Resolve operator artifact paths relative to the manifest."""
    manifest = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    return manifest.model_copy(
        update={
            "installed_manifest": path.parent / manifest.installed_manifest,
            "entitlement_evidence": path.parent / manifest.entitlement_evidence,
        }
    )
