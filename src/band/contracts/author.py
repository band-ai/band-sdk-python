"""Native author claims and immutable completions.

The platform redelivers a running job only to the claim ID that claimed it, and
no job projection returns that ID. Callers durably retain each claim ID before
claiming and each completion's exact bytes until the platform acknowledges them.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

import httpx
from pydantic import Field

from band.contracts.errors import ContractProtocolError
from band.contracts.models import ContractConnection, FrozenModel, Identifier
from band.contracts.transport import ContractTransport
from band.contracts.wire import MAX_AUTHOR_BYTES, JsonObject, decode_json, encode_json
from remote_agent.band.authorack import acknowledges_completion


class AuthorCompletion(FrozenModel):
    """Keep the exact production-encoded result for an explicit retry."""

    job: Identifier
    attempt: Annotated[int, Field(ge=0, le=2)]
    encoded: bytes = Field(repr=False)

    @classmethod
    def success(cls, job: str, *, attempt: int, result: JsonObject) -> AuthorCompletion:
        return cls(
            job=job,
            attempt=attempt,
            encoded=encode_json(
                {"attempt": attempt, "result": result}, limit=MAX_AUTHOR_BYTES
            ),
        )

    @classmethod
    def failure(
        cls, job: str, *, attempt: int, failure: str, held: bool = False
    ) -> AuthorCompletion:
        if not failure or len(failure.encode("utf-8")) > 2000:
            raise ValueError("Author failure text must contain 1 to 2000 UTF-8 bytes")
        return cls(
            job=job,
            attempt=attempt,
            encoded=encode_json(
                {"attempt": attempt, "failure": failure, "held": held},
                limit=MAX_AUTHOR_BYTES,
            ),
        )


class ContractAuthorClient(ContractTransport):
    """Call the native author API; the caller owns durable claim and retry state.

    Persist the stable claim ID before ``claim`` and reuse it after a crash: a
    new ID does not reattach a job that is already running. Keep a completion's
    exact ``AuthorCompletion`` until ``complete`` is acknowledged.
    """

    def __init__(
        self, connection: ContractConnection, *, http: httpx.AsyncClient | None = None
    ) -> None:
        if connection.authentication != "band-api-key":
            raise ValueError("The author API requires a Band agent API key")
        super().__init__(connection, http=http)

    async def claim(self, *, id: str) -> JsonObject:
        if not id:
            raise ValueError("An explicit stable claim ID is required")
        result = await self.request(
            "POST",
            "/api/v1/agent/contracts/authoring/claim",
            body=encode_json({"id": id}, limit=MAX_AUTHOR_BYTES),
            limit=MAX_AUTHOR_BYTES,
        )
        job = result.get("job")
        if job is not None and (
            not isinstance(job, dict)
            or not isinstance(job.get("id"), str)
            or type(job.get("attempt")) is not int
            or not isinstance(job.get("request"), dict)
        ):
            raise ContractProtocolError("Author claim returned an invalid job")
        return result

    async def complete(self, completion: AuthorCompletion) -> JsonObject:
        body = decode_json(completion.encoded)
        if (
            body.get("attempt") != completion.attempt
            or len(completion.encoded) > MAX_AUTHOR_BYTES
        ):
            raise ContractProtocolError(
                "Completion body differs from its exact attempt"
            )
        result = await self.request(
            "POST",
            f"/api/v1/agent/contracts/authoring/{quote(completion.job, safe='')}/result",
            body=completion.encoded,
            limit=MAX_AUTHOR_BYTES,
        )
        if not acknowledges_completion(result, completion.job, completion.attempt):
            raise ContractProtocolError(
                "Author response did not acknowledge the submitted completion"
            )
        return result
