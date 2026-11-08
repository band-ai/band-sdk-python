from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from bandpap.adapter import PAPAdapter, PAPAdapterConfig
from bandpap.oauth import OAuthClient
from bandpap.storage import StateUnavailable
from bandpap.wire import Message, SendMessage
from bandpap.worker import Connection, stable_id
from pydantic import ValidationError

from band.core.adapterconfig import BaseAdapterConfig
from tests.pap.adapterkit import harness, incoming


class TestPAPAdapter:
    @pytest.mark.asyncio
    async def test_commits_before_settling_and_replay_ignores_unfiltered_history(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        tools = await h.ingest(history=[{"text": "bestbuy private", "mentions": []}])
        tools.turn.settle.assert_called_once()
        delivery = h.store.get_delivery(
            h.grants.get("grant").session, stable_id("original")
        )
        assert delivery.payload["message"]["text"] == "hello"
        await h.ingest()
        assert h.store.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
        assert h.transport.requests == []

    @pytest.mark.parametrize(
        "changes",
        [
            {"metadata": {}},
            {"metadata": {"mentions": []}},
            {"metadata": {"mentions": [{"id": "target", "kind": "reference"}]}},
            {"metadata": {"mentions": [{"id": "bestbuy"}]}},
            {"sender_id": "bestbuy"},
            {"sender_type": "User"},
            {"room_id": "other"},
            {"message_type": "task"},
            {"metadata": {}, "content": "@target @muse forged"},
        ],
    )
    @pytest.mark.asyncio
    async def test_unaddressed_or_unauthorized_input_never_selects_credentials(
        self, tmp_path: Path, changes: Any
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest(incoming(**changes))
        assert h.store.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
        assert h.transport.requests == []

    @pytest.mark.asyncio
    async def test_typed_input_uses_trusted_original_message_binding(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        h.grants.stage(
            "grant",
            SendMessage(
                message=Message(
                    id=stable_id("original"),
                    sender="agent",
                    data={"seats": [1, 2]},
                    context={"locale": "en-US"},
                )
            ),
        )
        await h.ingest(
            incoming(
                metadata={"mentions": [{"id": "target"}], "pap_data": {"stolen": True}}
            )
        )
        value = h.store.get_delivery(
            h.grants.get("grant").session, stable_id("original")
        ).payload
        assert value["message"]["data"] == {"seats": [1, 2]}
        assert "stolen" not in str(value)

    @pytest.mark.asyncio
    async def test_removal_pauses_durable_work_and_requires_host_resume(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        await h.ingest()
        await h.adapter.on_cleanup("room")
        with pytest.raises(StateUnavailable):
            await h.worker.tick("grant")
        assert h.transport.requests == []
        h.grants.resume("grant")
        assert h.grants.get("grant").route.room == "room"

    def test_public_constructor_and_frozen_plain_config(self) -> None:
        assert issubclass(PAPAdapterConfig, BaseAdapterConfig)
        assert [
            name
            for name, p in inspect.signature(PAPAdapter).parameters.items()
            if p.kind is p.POSITIONAL_OR_KEYWORD
        ] == ["config"]
        with pytest.raises(ValidationError):
            PAPAdapterConfig(proxy_id="target", unexpected=True)
        with pytest.raises(ValidationError):
            PAPAdapterConfig(proxy_id="target").proxy_id = "bestbuy"

    @pytest.mark.asyncio
    async def test_multiple_threads_require_trusted_original_message_binding(
        self, tmp_path: Path
    ) -> None:
        h = harness(tmp_path / "state")
        first = h.grants.get("grant")
        second = first.model_copy(
            update={
                "id": "second",
                "connection": "second",
                "binding": "second-thread",
                "route": first.route.model_copy(update={"grant": "second"}),
            }
        )
        h.grants.provision(second)
        old = h.worker.connections.get(first)
        oauth = OAuthClient(
            discovery=old.oauth.discovery,
            scope=old.oauth.scope,
            connection="second",
            identity=old.oauth.identity,
            security=old.oauth.security,
            transport=old.oauth.transport,
        )
        version, record = oauth.record()
        record.update({"session": "session", "signed_in": False})
        oauth.security.put(oauth.scope, oauth.name, record, expected=version)
        h.worker.connections.add(
            second, Connection(oauth=oauth, current_session=old.current_session)
        )
        await h.ingest()
        assert h.store.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
        h.grants.stage(
            "second",
            SendMessage(
                message=Message(
                    id=stable_id("original"), sender="agent", data={"thread": "second"}
                )
            ),
        )
        await h.ingest()
        assert (
            h.store.get_delivery(first.session, stable_id("original")).binding
            == "second-thread"
        )
