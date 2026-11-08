from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bandpap.http import Response
from bandpap.storage import StateUnavailable
from bandpap.wire import Message

from tests.pap.adapterkit import accepted, grant, harness, reply
from tests.pap.kit import session


class TestPAPControls:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "action,status,code", [("handoff", "queued", 202), ("close", "closed", 200)]
    )
    async def test_control_accepts_protocol_response_without_repeated_conversation_id(
        self, tmp_path: Path, action: Any, status: Any, code: Any
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.extend(
            [
                Response(201, {}, json.dumps(accepted()).encode()),
                reply(),
                Response(
                    code,
                    {},
                    json.dumps({"status": status, "responder": "agent"}).encode(),
                ),
            ]
        )
        await h.worker.tick("grant")
        response = await h.worker.control("grant", action)
        assert (response.conversation_id, response.status) == ("cnv_one", status)
        assert h.transport.requests[-1]["url"].endswith("/" + action)

    @pytest.mark.asyncio
    async def test_direct_uses_current_account_and_requires_human_same_room(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        connection = h.worker.connections.get(h.grants.get("grant"))
        version, record = connection.oauth.record()
        record["signed_in"] = True
        connection.oauth.security.put(
            connection.oauth.scope, connection.oauth.name, record, expected=version
        )
        connection.current_session.return_value = session(account="account")
        await h.ingest()
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        await h.worker.tick("grant")
        parent = h.grants.get("grant")
        child = grant(
            id="direct",
            session=session(account="account"),
            connection="human",
            binding="direct-thread",
            sender_type="User",
            route=parent.route.model_copy(
                update={"source": "person", "recipient": "person", "grant": "direct"}
            ),
        )
        first = Message(
            id="direct-first", sender="human", text="I want to speak privately"
        )
        await h.worker.direct("grant", child, first)
        assert (
            h.store.get_delivery(session(account="account"), "direct-first").binding
            == "direct-thread"
        )
        assert (
            h.store.db.execute(
                "SELECT parent_id FROM pap_direct WHERE grant_id='direct'"
            ).fetchone()[0]
            == "cnv_one"
        )
        with pytest.raises(StateUnavailable):
            await h.worker.direct(
                "grant", child, first.model_copy(update={"sender": "agent"})
            )
        with pytest.raises(StateUnavailable):
            await h.worker.direct(
                "grant",
                child.model_copy(
                    update={"route": child.route.model_copy(update={"room": "other"})}
                ),
                first,
            )

    @pytest.mark.asyncio
    async def test_permanent_refusal_is_visible_and_waits_for_explicit_retry(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.append(
            Response(409, {}, b'{"error":"conversation_closed"}')
        )
        await h.worker.tick("grant")
        assert h.worker.status("grant")[2] == "conversation_closed"
        assert "needs attention" in h.sender.sent[0].text
        await h.worker.tick("grant")
        assert len(h.transport.requests) == 1
        h.worker.retry("grant")
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        await h.worker.tick("grant")
        assert len(h.transport.requests) == 3
