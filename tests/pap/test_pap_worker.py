from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bandpap.http import Response
from bandpap.storage import StateUnavailable
from bandpap.worker import stable_id

from tests.pap.adapterkit import accepted, harness, reply
from tests.pap.kit import session


class TestPAPWorker:
    @pytest.mark.asyncio
    async def test_roundtrip_keeps_company_private_and_preserves_structured_record(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        await h.worker.tick("grant")
        assert len(h.sender.sent) == 1
        projected = h.sender.sent[0]
        assert (projected.room, projected.proxy, projected.recipients) == (
            "room",
            "target",
            ("muse",),
        )
        assert "123" not in projected.text
        assert projected.record is None
        reference = projected.text.split("[PAP record: ")[1].removesuffix("]")
        private = await h.outbox.record(reference, room="room", actor="muse")
        assert private["message"]["data"] == {"price": 123}
        with pytest.raises(StateUnavailable):
            await h.outbox.record(reference, room="room", actor="bestbuy")
        assert h.store.conversation(session(), "thread").cursor == "evt_reply"

    @pytest.mark.asyncio
    async def test_lost_acceptance_retries_original_message_id(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.append(TimeoutError())
        with pytest.raises(TimeoutError):
            await h.worker.tick("grant")
        assert (
            h.store.get_delivery(session(), stable_id("original")).state == "uncertain"
        )
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        await h.worker.tick("grant")
        posts = [
            request["document"]
            for request in h.transport.requests
            if request["method"] == "POST"
        ]
        assert posts[0] == posts[1]
        assert (
            h.store.get_delivery(session(), stable_id("original")).state == "delivered"
        )

    @pytest.mark.asyncio
    async def test_projection_replay_accepts_once_after_cursor_commit(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        await h.worker.tick("grant")
        h.store.db.execute("DELETE FROM pap_projected")
        await h.worker.project(h.grants.get("grant"), session())
        await h.outbox.flush(proxy="target")
        assert len(h.sender.sent) == 1

    @pytest.mark.asyncio
    async def test_wrong_account_session_stops_before_company_delivery(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        connection = h.worker.connections.get(h.grants.get("grant"))
        connection.current_session.return_value = session(account="other")
        with pytest.raises(StateUnavailable):
            await h.worker.tick("grant")
        assert h.transport.requests == []

    @pytest.mark.asyncio
    async def test_rate_limit_retains_delivery_and_restart_retry_time(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.extend(
            [Response(429, {"retry-after": "30"}, b'{"error":"rate_limited"}')]
        )
        await h.worker.tick("grant")
        delivery = h.store.get_delivery(session(), stable_id("original"))
        assert delivery.state == "ready" and delivery.failure.status_code == 429
        await h.worker.tick("grant")
        assert len(h.transport.requests) == 1

    @pytest.mark.asyncio
    async def test_account_use_binds_history_and_signed_out_cannot_replay(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        connection = h.worker.connections.get(h.grants.get("grant"))
        version, record = connection.oauth.record()
        record["signed_in"] = True
        connection.oauth.security.put(
            connection.oauth.scope, connection.oauth.name, record, expected=version
        )
        connection.current_session.return_value = session(account="account-a")
        await h.ingest()
        assert (
            h.store.conversation(session(account="account-a"), "thread").account
            == "account-a"
        )
        version, record = connection.oauth.record()
        record["signed_in"] = False
        connection.oauth.security.put(
            connection.oauth.scope, connection.oauth.name, record, expected=version
        )
        connection.current_session.return_value = session()
        with pytest.raises(StateUnavailable):
            await h.ingest()
        with pytest.raises(StateUnavailable):
            await h.worker.tick("grant")
        assert h.transport.requests == []
        version, record = connection.oauth.record()
        record["signed_in"] = True
        connection.oauth.security.put(
            connection.oauth.scope, connection.oauth.name, record, expected=version
        )
        connection.current_session.return_value = session(account="account-b")
        with pytest.raises(StateUnavailable):
            await h.ingest()

    @pytest.mark.asyncio
    async def test_revoked_proxy_never_replays_persisted_reply(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        h.transport.responses.extend(
            [Response(201, {}, json.dumps(accepted()).encode()), reply()]
        )
        h.sender.error = TimeoutError()
        await h.worker.tick("grant")
        h.grants.revoke("grant")
        with pytest.raises(StateUnavailable):
            await h.worker.tick("grant")
        assert len(h.sender.sent) == 1

    @pytest.mark.asyncio
    async def test_revocation_while_request_acquires_token_prevents_company_post(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        oauth = h.worker.connections.get(h.grants.get("grant")).oauth
        original = oauth.token
        calls = 0

        async def token(**kwargs) -> Any:
            nonlocal calls
            value = await original(**kwargs)
            calls += 1
            if calls == 2:
                h.grants.revoke("grant")
            return value

        monkeypatch.setattr(oauth, "token", token)
        with pytest.raises(StateUnavailable):
            await h.worker.tick("grant")
        assert calls == 2
        assert h.transport.requests == []
