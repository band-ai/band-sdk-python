"""Pass every assessed execution mode to the program host without substitution."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from band.contracts.programs import ContractProgramHostClient, ProgramStart
from tests.contracts.programs.peer import ProgramPeer


class TestModes:
    @pytest.mark.parametrize("mode", ["program", "hybrid", "llm"])
    async def test_start_preserves_the_selected_assessed_mode(
        self, mode: Literal["program", "hybrid", "llm"]
    ) -> None:
        peer = ProgramPeer()
        start = ProgramStart(
            id="start",
            assessment_id="assessed",
            mode=mode,
            journal=True,
            target_bytes=1_000_000,
            minimum_steps=1,
        )
        async with (
            peer.client() as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            await client.start(start)
        assert peer.requests[0]["request"] == {
            "id": "start",
            "assessment_id": "assessed",
            "mode": mode,
            "journal": True,
            "target_bytes": 1_000_000,
            "minimum_steps": 1,
        }

    @pytest.mark.parametrize("mode", ["automatic", "LLM", None])
    def test_unknown_modes_are_rejected(self, mode: object) -> None:
        with pytest.raises(ValidationError):
            ProgramStart.model_validate(
                {
                    "id": "start",
                    "assessment_id": "assessed",
                    "mode": mode,
                    "journal": True,
                    "target_bytes": 1_000_000,
                    "minimum_steps": 1,
                }
            )
