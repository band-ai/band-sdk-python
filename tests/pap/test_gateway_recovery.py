"""Durable inbound handoffs survive restart and keep unrelated participants moving."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from tests.pap.gatewaykit import BandPeer, GatewayHarness, incoming, message, reply


class TestGatewayRecovery:
    async def test_acknowledged_input_and_lost_handoff_response_recover_by_work_id(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "gateway.db"
        band = BandPeer()
        first = GatewayHarness(path, band=band)
        access = first.access()
        response = await first.request(
            access, "POST", "/poppy/conversations", value=message()
        )
        band.failure = True
        assert await first.gateway.flush() == 0
        assert len(band.accepted) == 1
        work_id = next(iter(band.accepted))
        await first.close()
        second = GatewayHarness(path, band=band)
        renewed = second.access(session_id="renewed")
        second.gateway.clock = lambda: time.time() + 1
        assert await second.gateway.flush(renewed) == 1
        assert list(band.accepted) == [work_id]
        retry = await second.request(
            renewed, "POST", "/poppy/conversations", value=message()
        )
        assert retry.status_code == 201 and retry.json() == response.json()
        await second.close()

    async def test_revoked_owner_does_not_block_other_same_room_participant(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        alice, bob = kit.access(), kit.access("bob")
        await kit.gateway.accept(alice, incoming())
        await kit.gateway.accept(bob, incoming())
        kit.band.revoked.add(alice.session.session_id)
        assert await kit.gateway.flush() == 1
        accepted = list(kit.band.accepted.values())
        assert accepted[0].grant.proxy == "proxy-bob"
        assert accepted[0].grant.room == "one-room"
        assert (
            kit.store.db.execute(
                "SELECT count(*) FROM gateway_outbox WHERE state='blocked'"
            ).fetchone()[0]
            == 1
        )
        await kit.close()

    async def test_failed_commit_rolls_back_conversation_event_and_handoff(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")

        def deny_commit(action: int, argument: str | None, *unused: object) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT"
                else sqlite3.SQLITE_OK
            )

        kit.store.db.set_authorizer(deny_commit)
        with pytest.raises(sqlite3.DatabaseError):
            await kit.gateway.accept(kit.access(), incoming())
        kit.store.db.set_authorizer(None)
        for table in (
            "gateway_conversations",
            "gateway_messages",
            "gateway_events",
            "gateway_outbox",
        ):
            assert (
                kit.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )
        await kit.close()

    async def test_account_binding_survives_session_renewal_and_rejects_signout(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        owner = kit.access()
        _, response = await kit.gateway.accept(owner, incoming())
        id_ = response["conversation_id"]
        signed_in = kit.access(account="account", session_id="signed-in")
        await kit.gateway.bind_account(signed_in, id_)
        renewed = kit.access(account="account", session_id="renewed")
        assert (await kit.gateway.read(renewed, id_))["conversation_id"] == id_
        signed_out = await kit.request(
            owner, "GET", f"/poppy/conversations/{id_}/events"
        )
        assert (
            signed_out.status_code == 403
            and signed_out.json()["error"] == "sign_in_required"
        )
        wrong = kit.access(account="different", session_id="wrong")
        denied = await kit.request(wrong, "GET", f"/poppy/conversations/{id_}/events")
        assert denied.status_code == 404
        other_user = kit.access(
            "bob", account="account", session_id="same-account-other-user"
        )
        assert (
            await kit.request(other_user, "GET", f"/poppy/conversations/{id_}/events")
        ).status_code == 404
        await kit.close()

    async def test_existing_message_retry_needs_original_authority_not_new_create_permission(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        original = await kit.gateway.accept(access, incoming())
        authorize = kit.band.authorize

        async def existing_only(caller, action, conversation):
            if action == "create" and conversation is None:
                raise PermissionError("No new conversations")
            return await authorize(caller, action, conversation)

        monkeypatch.setattr(kit.band, "authorize", existing_only)
        assert await kit.gateway.accept(access, incoming()) == original
        with pytest.raises(PermissionError):
            await kit.gateway.accept(access, incoming("msg_new"))
        await kit.close()

    async def test_failed_first_handoff_does_not_reorder_same_conversation(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        _, response = await kit.gateway.accept(access, incoming())
        await kit.gateway.accept(
            access, incoming("msg_two"), conversation_id=response["conversation_id"]
        )
        kit.band.failure = True
        assert await kit.gateway.flush() == 0
        assert [work.message.id for work in kit.band.accepted.values()] == ["msg_one"]
        kit.gateway.clock = lambda: time.time() + 1
        assert await kit.gateway.flush() == 2
        assert [work.message.id for work in kit.band.accepted.values()] == [
            "msg_one",
            "msg_two",
        ]
        await kit.close()

    async def test_changed_snapshot_is_reauthorized_before_read_projection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "gateway.db"
        first, second = GatewayHarness(path), GatewayHarness(path)
        access = first.access()
        _, response = await first.gateway.accept(access, incoming())
        id_ = response["conversation_id"]
        entered, release = asyncio.Event(), asyncio.Event()
        authorize = first.band.authorize

        async def working_only(caller, action, conversation):
            if action == "read":
                if conversation.status != "working":
                    raise PermissionError("New state requires another authority")
                entered.set()
                await release.wait()
            return await authorize(caller, action, conversation)

        monkeypatch.setattr(first.band, "authorize", working_only)
        pending = asyncio.create_task(first.gateway.read(access, id_))
        await entered.wait()
        await second.gateway.publish(
            access,
            id_,
            reply(),
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        release.set()
        with pytest.raises(PermissionError):
            await pending
        await first.close()
        await second.close()

    async def test_asgi_lifespan_owns_and_stops_handoff_worker(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        await kit.gateway.accept(kit.access(), incoming())
        async with kit.gateway.app.router.lifespan_context(kit.gateway.app):
            async with asyncio.timeout(1):
                while not kit.band.accepted:
                    await asyncio.sleep(0)
        assert len(kit.band.accepted) == 1
        await kit.close()

    async def test_account_transition_reauthorizes_pending_work_without_blocking_other_owner(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        _, response = await kit.gateway.accept(access, incoming())
        _, _ = await kit.gateway.accept(kit.access("bob"), incoming())
        signed_in = kit.access(account="account", session_id="signed-in")
        await kit.gateway.bind_account(signed_in, response["conversation_id"])
        assert await kit.gateway.flush() == 2
        assert {work.grant.proxy for work in kit.band.accepted.values()} == {
            "proxy-alice",
            "proxy-bob",
        }
        await kit.close()

    async def test_explicit_fresh_authority_resumes_paused_work_without_reviving_revocation(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        old = kit.access()
        _, response = await kit.gateway.accept(old, incoming())
        id_ = response["conversation_id"]
        kit.band.revoked.add(old.session.session_id)
        assert await kit.gateway.flush() == 0
        with pytest.raises(PermissionError):
            await kit.gateway.resume(old, id_)
        renewed = kit.access(session_id="renewed")
        await kit.gateway.resume(renewed, id_)
        assert await kit.gateway.flush() == 1
        assert await kit.gateway.flush() == 0
        assert len(kit.band.accepted) == 1
        await kit.close()
