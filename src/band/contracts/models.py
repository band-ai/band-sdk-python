"""Typed configuration and immutable request snapshots for contract APIs."""

from __future__ import annotations

import ipaddress
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

from band.contracts.wire import JsonObject, decode_json, encode_json

Identifier = Annotated[str, Field(min_length=1, max_length=1024)]
Nonnegative = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class WireRequest(FrozenModel):
    """Snapshot request data so nested caller edits cannot change an exact retry."""

    def model_post_init(self, context: Any) -> None:
        # Pin nested input before it can be mutated by a caller between retries.
        _ = self.encoded

    @cached_property
    def encoded(self) -> bytes:
        return encode_json(self.model_dump(mode="json", exclude_none=True))

    def body(self) -> JsonObject:
        return decode_json(self.encoded)


class ContractConnection(FrozenModel):
    """Configure an authenticated authority; HTTP is restricted to IP loopback."""

    url: str
    credential: SecretStr = Field(repr=False)
    authentication: Literal["band-api-key", "bearer"] = "band-api-key"
    timeout: Annotated[float, Field(gt=0, le=120)] = 10.0

    @field_validator("url")
    @classmethod
    def authority_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Provide an authority URL without userinfo, query or fragment"
            )
        if parsed.scheme == "https":
            return value.rstrip("/")
        if parsed.scheme == "http":
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if loopback:
                return value.rstrip("/")
        raise ValueError(
            "Contract authorities require HTTPS or explicit IP loopback HTTP"
        )

    @field_validator("credential")
    @classmethod
    def nonempty_credential(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value() or any(
            character in value.get_secret_value() for character in "\r\n"
        ):
            raise ValueError("Provide a nonempty contract credential")
        return value


class ContractIdentity(FrozenModel):
    profile: Literal["contract-room/http/1"]
    idempotent: Literal[True]
    identity: JsonObject
    actor: Identifier
    session: Identifier
    inbox_profile: Literal["contract-room/inbox/1"]


class ContractProposal(WireRequest):
    id: Identifier
    session: Identifier
    kind: Identifier
    payload: JsonObject
    expected_version: Nonnegative


class InboxPage(FrozenModel):
    """Expose native cursor and receipts; consuming a page never acknowledges chat."""

    cursor: Nonnegative
    events: list[JsonObject]
    head: JsonObject | None = None


class JournalQuery(WireRequest):
    after: Nonnegative = 0
    through: Nonnegative | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    kind: str | None = None
    query: str = ""

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 256:
            raise ValueError("A journal query is limited to 256 UTF-8 bytes")
        return value


class ProcessOptions(FrozenModel):
    startup_timeout: Annotated[float, Field(gt=0, le=900)] = 60.0
    shutdown_timeout: Annotated[float, Field(gt=0, le=900)] = 200.0
    operation_timeout: Annotated[float, Field(gt=0, le=900)] = 300.0
    max_output_bytes: Annotated[int, Field(ge=1024, le=16_777_216)] = 4_194_304


class RuntimeConfig(ProcessOptions):
    """Select a local runner explicitly, with a qualified absolute interpreter."""

    python: Path
    cwd: Path | None = None
    environment: dict[str, str] = Field(
        default_factory=lambda: {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    )

    @field_validator("python", "cwd")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Local runner paths must be absolute")
        return value


class ProcessResult(FrozenModel):
    returncode: int
    forced: bool = False
    stdout: bytes = Field(default=b"", repr=False)
    stderr: bytes = Field(default=b"", repr=False)
