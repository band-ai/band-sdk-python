"""Acknowledge the submitted completion when the native job has advanced."""

from __future__ import annotations

import json

import pytest
from pydantic import JsonValue

from band.contracts.author import AuthorCompletion, ContractAuthorClient
from band.contracts.errors import ContractProtocolError, ContractTransportError
from band.contracts.wire import JsonObject
from tests.contracts.author.platform import AuthorPeer
from tests.contracts.author.retry import RetryPeer


class TestAcknowledgement:
    @pytest.mark.parametrize("attempt", [0, 1])
    async def test_compiler_correction_acknowledges_the_submitted_attempt(
        self, attempt: int
    ) -> None:
        peer = AuthorPeer()
        artifact: JsonObject = {
            "source": "invalid source",
            "derivation": [],
            "unresolved": [],
        }
        queued = peer.project(
            attempt=attempt + 1,
            status="queued",
            result=artifact,
            diagnostics={"code": "invalid_source"},
        )
        peer.responses = [queued]
        completion = AuthorCompletion.success(
            peer.job, attempt=attempt, result=artifact
        )

        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            acknowledged = await client.complete(completion)

        assert acknowledged == queued
        assert json.loads(peer.requests[0].content) == {
            "attempt": attempt,
            "result": artifact,
        }

    async def test_lost_ack_retry_accepts_the_current_projection_after_later_completion(
        self,
    ) -> None:
        peer = AuthorPeer()
        original: JsonObject = {
            "source": "first source",
            "derivation": [],
            "unresolved": [],
        }
        corrected: JsonObject = {
            "source": "corrected source",
            "derivation": [],
            "unresolved": [],
        }
        current = peer.project(attempt=2, status="ready", result=corrected)
        peer.responses = [None, current]
        completion = AuthorCompletion.success(peer.job, attempt=0, result=original)

        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            with pytest.raises(ContractTransportError):
                await client.complete(completion)
            acknowledged = await client.complete(completion)

        assert acknowledged == current
        assert len(peer.requests) == 2
        assert (
            peer.requests[0].content == peer.requests[1].content == completion.encoded
        )
        assert json.loads(peer.requests[1].content) == {
            "attempt": 0,
            "result": original,
        }

    @pytest.mark.parametrize(
        ("field", "value"), [("id", "another-job"), ("status", "unknown")]
    )
    async def test_current_projection_still_requires_the_job_and_an_acknowledged_status(
        self, field: str, value: str
    ) -> None:
        peer = AuthorPeer()
        response = peer.project(attempt=1, status="queued")
        response[field] = value
        peer.responses = [response]

        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            with pytest.raises(ContractProtocolError):
                await client.complete(
                    AuthorCompletion.success(
                        peer.job, attempt=0, result={"source": "initial"}
                    )
                )

        assert len(peer.requests) == 1

    @pytest.mark.parametrize("attempt", [0, 1])
    async def test_retry_acknowledges_original_receipt_after_next_attempt_is_claimed(
        self, attempt: int
    ) -> None:
        peer = RetryPeer(attempt)
        completion = AuthorCompletion.success(
            peer.job,
            attempt=attempt,
            result={"source": "invalid source", "derivation": [], "unresolved": []},
        )
        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            with pytest.raises(ContractTransportError):
                await client.complete(completion)
            assert peer.status == "queued"
            claimed = await client.claim(id="next-attempt")
            job = claimed["job"]
            assert isinstance(job, dict)
            assert job["attempt"] == attempt + 1
            assert peer.status == "running"
            acknowledged = await client.complete(completion)
        assert acknowledged["attempt"] == attempt + 1
        assert acknowledged["status"] == "running"
        assert len(peer.requests) == 3
        assert (
            peer.requests[0].content == peer.requests[2].content == completion.encoded
        )
        assert json.loads(peer.requests[1].content) == {"id": "next-attempt"}

    @pytest.mark.parametrize("current_attempt", [0, 1, -1, 3, True, "2", None])
    async def test_running_response_requires_a_later_valid_attempt(
        self, current_attempt: JsonValue
    ) -> None:
        peer = AuthorPeer()
        response = peer.project(attempt=1, status="running")
        response["attempt"] = current_attempt
        peer.responses = [response]
        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            with pytest.raises(ContractProtocolError):
                await client.complete(
                    AuthorCompletion.success(
                        peer.job, attempt=1, result={"source": "x"}
                    )
                )
