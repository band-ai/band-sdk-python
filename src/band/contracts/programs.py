"""Explicit platform-granted program-host operations."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import Field, field_validator, model_validator

from band.contracts.errors import ContractIdentityError
from band.contracts.models import (
    ContractConnection,
    Identifier,
    JournalQuery,
    WireRequest,
)
from band.contracts.transport import ContractTransport
from band.contracts.wire import JsonObject, encode_json


class ProgramScope(WireRequest):
    """Bind the trusted platform context; ordinary participant tools cannot create it."""

    room: Identifier
    session: Identifier
    actor: str = Field(pattern=r"^User:.+")
    context: JsonObject


class ProgramAssessment(WireRequest):
    id: Identifier
    role: Identifier
    author_id: Identifier


class ProgramStart(WireRequest):
    id: Identifier
    assessment_id: Identifier
    mode: Literal["program", "hybrid"]
    journal: bool
    target_bytes: int = Field(ge=1_000_000, le=500_000_000)
    minimum_steps: int = Field(ge=1, le=1000)


class ProgramTask(WireRequest):
    id: Identifier
    kind: Identifier
    payload: JsonObject


class ProgramControl(WireRequest):
    id: Identifier
    action: Literal[
        "stop", "wake", "takeover", "resume", "repair", "retry", "reconcile"
    ]
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def bounded_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        reason = value.strip()
        if len(reason.encode("utf-8")) > 2000:
            raise ValueError("An operator reason is limited to 2000 UTF-8 bytes")
        return reason

    @model_validator(mode="after")
    def reason_for_control(self) -> ProgramControl:
        if self.action != "stop" and not self.reason:
            raise ValueError("A non-stop control requires its bounded operator reason")
        return self


class ProgramReplay(WireRequest):
    id: Identifier


class ContractProgramHostClient(ContractTransport):
    """Use the host's platform control grant, never an ordinary participant credential."""

    def __init__(
        self,
        connection: ContractConnection,
        *,
        scope: ProgramScope,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if connection.authentication != "bearer":
            raise ValueError(
                "Program host operations require the platform bearer grant"
            )
        super().__init__(connection, http=http)
        self._scope = scope

    async def _operation(self, operation: str, request: JsonObject) -> JsonObject:
        scope = self._scope.body()
        envelope = {
            "profile": "band-contract/program-host-request/1",
            **scope,
            "operation": operation,
            "request": request,
        }
        result = await self.request(
            "POST", "/v1/operations", body=encode_json(envelope)
        )
        if (
            result.get("profile") != "band-contract/program/1"
            or result.get("room") != scope["room"]
            or result.get("session") != scope["session"]
        ):
            raise ContractIdentityError(
                "Program host response differs from the requested scope"
            )
        return result

    async def assess(self, request: ProgramAssessment) -> JsonObject:
        return await self._operation("assess", request.body())

    async def start(self, request: ProgramStart) -> JsonObject:
        return await self._operation("start", request.body())

    async def status(self) -> JsonObject:
        return await self._operation("status", {})

    async def task(self, request: ProgramTask) -> JsonObject:
        return await self._operation("task", request.body())

    async def control(self, request: ProgramControl) -> JsonObject:
        return await self._operation("control", request.body())

    async def journal(self, query: JournalQuery | None = None) -> JsonObject:
        return await self._operation("journal", (query or JournalQuery()).body())

    async def replay(self, request: ProgramReplay) -> JsonObject:
        return await self._operation("replay", request.body())
