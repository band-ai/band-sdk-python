"""Start participants with explicit bounded history retention settings."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from band.contracts.programs import ContractProgramHostClient, ProgramStart
from tests.contracts.programs.peer import ProgramPeer


class TestRetention:
    @pytest.mark.parametrize(
        ("target_bytes", "minimum_steps"),
        [(1_000_000, 1), (500_000_000, 1000), (12_000_000, 9)],
        ids=["minimum", "maximum", "operator-budget"],
    )
    async def test_start_retries_preserve_the_operator_retention_budget(
        self, target_bytes: int, minimum_steps: int
    ) -> None:
        peer = ProgramPeer()
        start = ProgramStart(
            id="start",
            assessment_id="assessed",
            mode="program",
            journal=True,
            target_bytes=target_bytes,
            minimum_steps=minimum_steps,
        )
        async with (
            peer.client() as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            await client.start(start)
            await client.start(start)
        assert peer.requests[0] == peer.requests[1]
        assert peer.requests[0]["operation"] == "start"
        assert peer.requests[0]["request"] == {
            "id": "start",
            "assessment_id": "assessed",
            "mode": "program",
            "journal": True,
            "target_bytes": target_bytes,
            "minimum_steps": minimum_steps,
        }

    def test_start_requires_explicit_retention_settings(self) -> None:
        with pytest.raises(ValidationError) as error:
            ProgramStart.model_validate(
                {
                    "id": "start",
                    "assessment_id": "assessed",
                    "mode": "program",
                    "journal": True,
                }
            )
        assert {item["loc"] for item in error.value.errors()} == {
            ("target_bytes",),
            ("minimum_steps",),
        }

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("target_bytes", 999_999),
            ("target_bytes", 500_000_001),
            ("target_bytes", True),
            ("target_bytes", "1000000"),
            ("minimum_steps", 0),
            ("minimum_steps", 1001),
            ("minimum_steps", True),
            ("minimum_steps", "1"),
        ],
    )
    def test_out_of_range_or_noninteger_retention_is_rejected(
        self, field: str, value: Any
    ) -> None:
        data: dict[str, Any] = {
            "id": "start",
            "assessment_id": "assessed",
            "mode": "program",
            "journal": True,
            "target_bytes": 1_000_000,
            "minimum_steps": 1,
        }
        data[field] = value
        with pytest.raises(ValidationError) as error:
            ProgramStart.model_validate(data)
        assert [item["loc"] for item in error.value.errors()] == [(field,)]
