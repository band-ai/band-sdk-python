"""Offline protocol integration through real adapter, DPoP gateway and Band outbox."""

from __future__ import annotations

import asyncio

import pytest
from bandpap.storage import StateStore, StateUnavailable
from bandpap.worker import stable_id

from tests.pap.roundtripkit import ROOM, Roundtrip


class TestRoundtrip:
    async def test_two_companies_share_room_with_independent_authenticated_identities(
        self, tmp_path
    ) -> None:
        harness = Roundtrip(tmp_path)
        try:
            for name in ("a", "b"):
                await harness.ingest(name)
            await asyncio.gather(
                *(
                    company.worker.tick(company.grant.id)
                    for company in harness.companies.values()
                )
            )
            for company in harness.companies.values():
                await company.gateway.flush()
            for name in ("a", "b"):
                await harness.answer(name, text="Answer from " + name)
            assert {post.room for post in harness.band.posts} == {ROOM}
            assert {post.proxy for post in harness.band.posts} == {
                "company-a",
                "company-b",
                "personal-a",
                "personal-b",
            }
            for name, company in harness.companies.items():
                assert len(company.band.accepted) == 1
                assert all(
                    url.startswith("https://" + name + ".company.example/")
                    for _, url, _ in company.channel.requests
                )
                replies = [
                    post
                    for post in harness.band.posts
                    if post.proxy == "company-" + name
                    and "Answer from " + name in post.text
                ]
                assert len(replies) == 1 and replies[0].recipients == ("muse",)
                assert '"private"' not in replies[0].text
                reference = replies[0].text.split("[PAP record: ")[1].removesuffix("]")
                record = await harness.outbox.record(reference, room=ROOM, actor="muse")
                assert record["message"]["data"] == {"private": name}
                with pytest.raises(StateUnavailable):
                    await harness.outbox.record(
                        reference,
                        room=ROOM,
                        actor="company-" + ("b" if name == "a" else "a"),
                    )
        finally:
            await harness.close()

    async def test_blocked_company_does_not_lock_other_company_progress(
        self, tmp_path
    ) -> None:
        harness = Roundtrip(tmp_path)
        blocked = harness.companies["a"]
        blocked.channel.entered, blocked.channel.release = (
            asyncio.Event(),
            asyncio.Event(),
        )
        try:
            await harness.ingest("a")
            await harness.ingest("b")
            pending = asyncio.create_task(blocked.worker.tick(blocked.grant.id))
            await asyncio.wait_for(blocked.channel.entered.wait(), 2)
            other = harness.companies["b"]
            await asyncio.wait_for(other.worker.tick(other.grant.id), 2)
            assert (
                harness.store.conversation(other.session, other.grant.binding).remote_id
                is not None
            )
            assert not pending.done()
            blocked.channel.release.set()
            await pending
        finally:
            if blocked.channel.release is not None:
                blocked.channel.release.set()
            await harness.close()

    async def test_lost_acceptance_recovers_same_message_id_and_cursor_survives_restart(
        self, tmp_path
    ) -> None:
        harness = Roundtrip(tmp_path)
        company = harness.companies["a"]
        try:
            await harness.ingest("a")
            company.channel.lose_first_acceptance = True
            with pytest.raises(TimeoutError):
                await company.worker.tick(company.grant.id)
            message_id = stable_id("request-a")
            assert (
                harness.store.get_delivery(company.session, message_id).state
                == "uncertain"
            )
            assert (
                company.gateway_store.db.execute(
                    "SELECT COUNT(*) FROM gateway_conversations"
                ).fetchone()[0]
                == 1
            )
            await company.worker.tick(company.grant.id)
            posts = [
                document
                for method, _, document in company.channel.requests
                if method == "POST"
            ]
            assert posts[0] == posts[1]
            assert (
                company.gateway_store.db.execute(
                    "SELECT COUNT(*) FROM gateway_messages"
                ).fetchone()[0]
                == 1
            )
            await company.gateway.flush()
            await harness.answer("a")
            cursor = harness.store.conversation(
                company.session, company.grant.binding
            ).cursor
            assert cursor is not None
            reopened = StateStore(tmp_path / "personal.db")
            assert (
                reopened.conversation(company.session, company.grant.binding).cursor
                == cursor
            )
            assert (
                reopened.get_delivery(company.session, message_id).state == "delivered"
            )
            reopened.close()
            delivered = len(harness.band.posts)
            harness.store.db.execute(
                "DELETE FROM pap_projected WHERE grant_id=?", (company.grant.id,)
            )
            await company.worker.project(company.grant, company.session)
            await harness.outbox.flush(proxy=company.grant.route.proxy)
            assert len(harness.band.posts) == delivered
        finally:
            await harness.close()

    async def test_inbound_personal_agent_reaches_outbound_company_and_reads_correlated_reply(
        self, tmp_path
    ) -> None:
        harness = Roundtrip(tmp_path)
        try:
            page = await harness.bridge_roundtrip()
            company_messages = [
                event["message"]
                for event in page["events"]
                if event["type"] == "message" and event["message"]["role"] == "company"
            ]
            assert len(company_messages) == 1
            assert "Company A answered through the room" in company_messages[0]["text"]
            assert company_messages[0]["data"] == {"answer": "private-output"}
            routes = {(post.proxy, post.recipients) for post in harness.band.posts}
            assert ("inbound-personal", ("company-a",)) in routes
            assert ("company-a", ("inbound-personal",)) in routes
            assert all(post.room == ROOM for post in harness.band.posts)
            assert all(post.proxy != "company-b" for post in harness.band.posts)
            downstream = [
                document
                for method, _, document in harness.companies["a"].channel.requests
                if method == "POST"
            ]
            assert downstream[0] is not None
            assert downstream[0]["message"]["data"] == {"request": "private-input"}
            assert harness.companies["b"].channel.requests == []
        finally:
            await harness.close()
