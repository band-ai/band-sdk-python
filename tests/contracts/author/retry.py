"""Model the native compiler retry receipt and intervening author claim."""

from __future__ import annotations

import json
from uuid import UUID

import httpx

from band.contracts.wire import JsonObject
from tests.contracts.author.platform import AuthorPeer

NEXT_CLAIM = "7a8c96d0-5db9-4f1b-887c-774c52f347cd"
ORIGINAL_CLAIM = "2566700c-66ad-4a72-9f5a-ce315787b04b"


class RetryPeer(AuthorPeer):
    def __init__(self, attempt: int = 0, *, queued: bool = False) -> None:
        super().__init__()
        self.initial_attempt = attempt
        self.current_attempt = attempt
        self.status = "queued" if queued else "running"
        self.active_claim: str | None = None if queued else ORIGINAL_CLAIM
        self.receipt: JsonObject | None = None
        self.submissions: list[JsonObject] = []

    def native(self, operation: str, body: JsonObject) -> JsonObject:
        if operation == "claim":
            claim = body["id"]
            assert isinstance(claim, str)
            assert str(UUID(claim)) == claim
            if self.status != "queued" and self.active_claim != claim:
                return {"job": None}
            self.active_claim = claim
            self.status = "running"
            return {
                "job": {
                    "id": self.job,
                    "attempt": self.current_attempt,
                    "request": {"operation": "generate", "history": []},
                }
            }
        assert operation == self.job + "/result"
        self.submissions.append(json.loads(json.dumps(body)))
        if self.receipt is None:
            assert body["attempt"] == self.initial_attempt
            self.receipt = self.submissions[-1]
            self.current_attempt += 1
            self.status = "queued"
            self.active_claim = None
            raise TimeoutError("Compiler receipt committed; response lost")
        assert body == self.receipt
        result = self.receipt["result"]
        assert isinstance(result, dict)
        return self.project(
            attempt=self.current_attempt,
            status=self.status,
            result=result,
            diagnostics={"code": "invalid_source"},
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "a" * 32
        prefix = "/api/v1/agent/contracts/authoring/"
        assert request.url.path.startswith(prefix)
        self.requests.append(request)
        try:
            result = self.native(
                request.url.path.removeprefix(prefix), json.loads(request.content)
            )
        except TimeoutError:
            raise httpx.ReadTimeout("Response lost", request=request) from None
        return httpx.Response(200, json=result)
