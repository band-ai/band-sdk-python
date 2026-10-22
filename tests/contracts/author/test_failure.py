"""Keep held and failed author messages within the native UTF-8 boundary."""

from __future__ import annotations

import json

import pytest

from band.contracts.author import AuthorCompletion, ContractAuthorClient
from tests.contracts.author.platform import AuthorPeer


class TestFailure:
    @pytest.mark.parametrize("held", [False, True], ids=["failed", "held"])
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param("a", id="one-byte"),
            pytest.param("a" * 2000, id="ascii-boundary"),
            pytest.param("é" * 1000, id="two-byte-boundary"),
            pytest.param("💡" * 500, id="four-byte-boundary"),
        ],
    )
    async def test_native_boundary_is_delivered_without_truncation(
        self, held: bool, failure: str
    ) -> None:
        peer = AuthorPeer()
        response = peer.project(
            attempt=0,
            status="held" if held else "failed",
            result={"error": failure},
            diagnostics=failure,
        )
        peer.responses = [response]
        completion = AuthorCompletion.failure(
            peer.job, attempt=0, failure=failure, held=held
        )

        async with (
            peer.client() as http,
            ContractAuthorClient(peer.connection(), http=http) as client,
        ):
            acknowledged = await client.complete(completion)

        assert acknowledged == response
        assert json.loads(peer.requests[0].content) == {
            "attempt": 0,
            "failure": failure,
            "held": held,
        }
        assert peer.requests[0].content == completion.encoded

    @pytest.mark.parametrize("held", [False, True], ids=["failed", "held"])
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param("", id="empty"),
            pytest.param("a" * 2001, id="ascii-one-byte-over"),
            pytest.param("é" * 1000 + "a", id="two-byte-one-byte-over"),
            pytest.param("💡" * 500 + "a", id="four-byte-one-byte-over"),
        ],
    )
    def test_native_boundary_is_rejected_before_a_completion_is_created(
        self, held: bool, failure: str
    ) -> None:
        with pytest.raises(ValueError):
            AuthorCompletion.failure("job", attempt=0, failure=failure, held=held)
