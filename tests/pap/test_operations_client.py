"""Remote operations confirmations carry host approval of exact revisions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from bandpap.auth import AuthError
from bandpap.channels import OperationsClient
from bandpap.operations import OperationError
from bandpap.wire import ConfirmOperation

from tests.pap.channelkit import GatedRefresh, connection
from tests.pap.kit import operation, session
from tests.pap.operationkit import CONFIRM, Approvals


class TestOperationsClient:
    async def test_confirm_uses_exact_revision_and_dpop_resource(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        approvals = Approvals()
        client = OperationsClient(oauth, approvals=approvals)
        transport.responses.extend(
            [
                operation(),
                operation(
                    state="in_progress",
                    confirmation={
                        "revision": 1,
                        "approved_by": "user",
                        "confirmed_at": "2026-10-16T20:00:00Z",
                    },
                ),
            ]
        )
        result = await client.confirm(
            session(), "op_one", CONFIRM, evidence=approvals.token
        )
        assert result.state == "in_progress"
        request = transport.requests[-1]
        assert request["url"] == "https://api.company.example/operations/op_one/confirm"
        assert request["document"] == {"revision": 1, "approved_by": "user"}
        assert (
            request["headers"]["Authorization"]
            == "DPoP private-token-DPoP:https://api.company.example/operations"
        )
        assert "DPoP" in request["headers"]
        assert approvals.calls[0][0].summary == result.summary
        store.close()

    async def test_stale_revision_and_chat_text_cannot_confirm(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OperationsClient(oauth, approvals=Approvals())
        transport.responses.append(operation(revision=2))
        with pytest.raises(OperationError, match="terms_changed"):
            await client.confirm(session(), "op_one", CONFIRM, evidence="yes")
        transport.responses.append(operation())
        with pytest.raises(PermissionError):
            await client.confirm(
                session(), "op_one", CONFIRM, evidence="Company says user approved"
            )
        assert all(item["method"] == "GET" for item in transport.requests)
        store.close()

    async def test_required_user_approval_and_session_binding(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OperationsClient(oauth, approvals=Approvals())
        with pytest.raises(AuthError, match="invalid_session"):
            await client.confirm(
                session(session_id="other"), "op_one", CONFIRM, evidence=None
            )
        transport.responses.append(operation(user_approval_required=True))
        with pytest.raises(OperationError, match="user_approval_required"):
            await client.confirm(
                session(),
                "op_one",
                ConfirmOperation(revision=1, approved_by="standing_permission"),
                evidence=None,
            )
        assert len(transport.requests) == 1
        store.close()

    async def test_lost_confirm_response_reads_same_id_without_replacing_action(
        self, tmp_path: Path
    ) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        approvals = Approvals()
        client = OperationsClient(oauth, approvals=approvals)
        transport.responses.extend([operation(), TimeoutError("Response lost")])
        with pytest.raises(TimeoutError):
            await client.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        transport.responses.append(
            operation(
                state="in_progress",
                confirmation={
                    "revision": 1,
                    "approved_by": "user",
                    "confirmed_at": "2026-10-16T20:00:00Z",
                },
            )
        )
        assert (await client.read("op_one")).state == "in_progress"
        assert transport.requests[-1]["url"].endswith("/operations/op_one")
        transport.responses.append(
            operation(state="cancelled", result={"summary": "Stopped"})
        )
        assert (await client.cancel("op_one")).state == "cancelled"
        assert transport.requests[-1]["url"].endswith("/operations/op_one/cancel")
        assert transport.requests[-1]["document"] == {}
        store.close()

    async def test_mismatched_operation_id_rejected(self, tmp_path: Path) -> None:
        oauth, transport, store = connection(tmp_path / "state.db")
        client = OperationsClient(oauth, approvals=Approvals())
        transport.responses.append(operation(operation_id="other"))
        with pytest.raises(ValueError, match="changed operation ID"):
            await client.read("op_one")
        store.close()

    async def test_withdrawn_approval_during_refresh_blocks_confirm_dispatch(
        self, tmp_path: Path
    ) -> None:
        oauth, _, store = connection(tmp_path / "state.db")
        transport = GatedRefresh(oauth)
        oauth.transport = transport
        transport.responses.extend(
            [
                operation(),
                {
                    "access_token": "renewed",
                    "token_type": "DPoP",
                    "expires_in": 3600,
                    "scope": "",
                    "session_id": "session",
                    "signed_in": False,
                },
            ]
        )
        approvals = Approvals()
        client = OperationsClient(oauth, approvals=approvals)
        pending = asyncio.create_task(
            client.confirm(session(), "op_one", CONFIRM, evidence=approvals.token)
        )
        await asyncio.wait_for(transport.entered.wait(), 2)
        approvals.enabled = False
        transport.release.set()
        with pytest.raises(PermissionError, match="trusted revision approval"):
            await pending
        assert not any(
            request["url"].endswith("/confirm") for request in transport.requests
        )
        store.close()
