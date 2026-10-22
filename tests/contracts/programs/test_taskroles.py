"""Choose an explicit counterparty for generic program tasks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from band.contracts.programs import ContractProgramHostClient, ProgramTask
from band.contracts.wire import JsonObject
from tests.contracts.programs.peer import ProgramPeer


class TestTaskRoles:
    async def test_task_retry_preserves_the_selected_sender_and_original_payload(
        self,
    ) -> None:
        peer = ProgramPeer()
        payload: JsonObject = {"text": "original"}
        task = ProgramTask(
            id="stable", sender_role="reviewer", kind="normalize", payload=payload
        )
        payload["text"] = "changed"
        async with (
            peer.client() as http,
            ContractProgramHostClient(
                peer.connection, scope=peer.scope, http=http
            ) as client,
        ):
            await client.task(task)
            await client.task(task)
        assert peer.requests[0] == peer.requests[1]
        assert peer.requests[0]["request"] == {
            "id": "stable",
            "sender_role": "reviewer",
            "kind": "normalize",
            "payload": {"text": "original"},
        }

    def test_legacy_task_omits_an_unspecified_sender(self) -> None:
        task = ProgramTask(id="legacy", kind="question", payload={"a": 1})
        assert task.body() == {"id": "legacy", "kind": "question", "payload": {"a": 1}}

    @pytest.mark.parametrize("role", ["", 7, True])
    def test_invalid_sender_role_is_rejected(self, role: object) -> None:
        with pytest.raises(ValidationError):
            ProgramTask.model_validate(
                {
                    "id": "stable",
                    "sender_role": role,
                    "kind": "normalize",
                    "payload": {},
                }
            )
