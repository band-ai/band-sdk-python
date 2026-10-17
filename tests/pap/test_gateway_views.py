"""External views enforce addressed provenance, projected rosters and lifecycle."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.pap.gatewaykit import ORIGIN, GatewayHarness, incoming, message, reply


class TestGatewayViews:
    async def test_wrong_source_or_recipient_never_publishes_and_roster_is_projected(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=100)
        access = kit.access()
        _, response = await kit.gateway.accept(access, incoming())
        id_ = response["conversation_id"]
        for source, recipients in [
            ("competitor", ("proxy-alice",)),
            ("company", ("proxy-bob",)),
        ]:
            with pytest.raises(PermissionError):
                await kit.gateway.publish(
                    access,
                    id_,
                    reply(),
                    source_id=source,
                    source_type="Agent",
                    recipients=recipients,
                )
        await kit.gateway.publish(
            access,
            id_,
            reply(sender="human"),
            source_id="company",
            source_type="User",
            recipients=("proxy-alice",),
        )
        kit.band.project["proxy-alice"] = (
            {"id": "proxy-alice", "name": "Personal agent"},
        )
        await kit.gateway.publish_roster(access, id_)
        page = await kit.gateway.read(access, id_)
        assert page["participants"] == [{"id": "proxy-alice", "name": "Personal agent"}]
        assert page["events"][-1]["participants"] == page["participants"]
        assert page["events"][1]["message"]["sender"] == "human"
        assert page["responder"] == "human" and page["status"] == "idle"
        await kit.close()

    async def test_handoff_close_and_parent_close_are_idempotent(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=100)
        access = kit.access()
        _, response = await kit.gateway.accept(access, incoming())
        id_ = response["conversation_id"]
        path = f"/poppy/conversations/{id_}"
        handoff = await kit.request(access, "POST", path + "/handoff", value={})
        assert handoff.status_code == 202 and handoff.json()["status"] == "queued"
        assert (
            await kit.request(access, "POST", path + "/handoff", value={})
        ).json() == handoff.json()
        child = (
            await kit.request(
                access,
                "POST",
                "/poppy/conversations",
                value=message("msg_direct", sender="human")
                | {"parent_conversation_id": id_},
            )
        ).json()["conversation_id"]
        closed = await kit.request(access, "POST", path + "/close", value={})
        before = kit.store.db.execute("SELECT count(*) FROM gateway_events").fetchone()[
            0
        ]
        repeated = await kit.request(access, "POST", path + "/close", value={})
        assert repeated.json() == closed.json()
        assert (
            kit.store.db.execute("SELECT count(*) FROM gateway_events").fetchone()[0]
            == before
        )
        assert (await kit.gateway.read(access, child))["status"] == "closed"
        assert (
            await kit.request(
                access, "POST", path + "/messages", value=message("msg_late")
            )
        ).status_code == 409
        assert (await kit.request(access, "GET", path + "/events")).status_code == 200
        await kit.close()

    async def test_bounded_sse_honors_last_event_id_without_reusing_proof(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=1)
        access = kit.access()
        _, response = await kit.gateway.accept(access, incoming())
        id_ = response["conversation_id"]
        first = await kit.gateway.read(access, id_)
        await kit.gateway.publish(
            access,
            id_,
            reply(),
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        result = await kit.request(
            access,
            "GET",
            f"/poppy/conversations/{id_}/events?cursor=unknown",
            headers={"Accept": "text/event-stream", "Last-Event-ID": first["cursor"]},
        )
        assert result.status_code == 200
        assert "id: evt_reply\n" in result.text
        assert '"type":"state"' in result.text
        assert '"id":"msg_one"' not in result.text
        await kit.close()

    async def test_company_closing_parent_also_closes_direct_child(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=100)
        access = kit.access()
        parent = (
            await kit.request(access, "POST", "/poppy/conversations", value=message())
        ).json()["conversation_id"]
        child = (
            await kit.request(
                access,
                "POST",
                "/poppy/conversations",
                value=message("msg_direct", sender="human")
                | {"parent_conversation_id": parent},
            )
        ).json()["conversation_id"]
        event = {
            "id": "evt_closed",
            "type": "state",
            "created_at": "2026-10-16T20:00:00Z",
            "status": "closed",
            "responder": "agent",
        }
        await kit.gateway.publish(
            access,
            parent,
            event,
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        await kit.gateway.publish(
            access,
            parent,
            event,
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        blocked = await kit.request(
            access,
            "POST",
            f"/poppy/conversations/{child}/messages",
            value=message("msg_late", sender="human"),
        )
        assert (
            blocked.status_code == 409
            and blocked.json()["error"] == "conversation_closed"
        )
        assert (await kit.gateway.read(access, child))["status"] == "closed"
        events = (await kit.gateway.read(access, parent))["events"]
        assert sum(item["type"] == "direct_closed" for item in events) == 1
        assert (
            sum(
                item["type"] == "state" and item["status"] == "closed"
                for item in events
            )
            == 1
        )
        await kit.close()

    async def test_visible_peer_does_not_become_a_delivery_recipient(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        kit.band.project["proxy-alice"] = (
            {"id": "coordinator", "name": "Coordinator"},
        )
        _, response = await kit.gateway.accept(access, incoming())
        await kit.gateway.flush()
        work = next(iter(kit.band.accepted.values()))
        assert work.grant.recipients == ("company",)
        assert (await kit.gateway.read(access, response["conversation_id"]))[
            "participants"
        ] == [{"id": "coordinator", "name": "Coordinator"}]
        with pytest.raises(ValueError, match="authorship"):
            await kit.gateway.publish(
                access,
                response["conversation_id"],
                reply(sender="human"),
                source_id="company",
                source_type="Agent",
                recipients=("proxy-alice",),
            )
        await kit.close()

    async def test_midstream_host_diagnostic_does_not_escape_to_asgi_logging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        _, created = await kit.gateway.accept(access, incoming())
        authorize = kit.band.authorize
        reads = 0

        async def fail_later(caller, action, conversation):
            nonlocal reads
            if action == "read":
                reads += 1
                if reads > 1:
                    raise RuntimeError("Bearer PRIVATE_SECRET host diagnostic")
            return await authorize(caller, action, conversation)

        monkeypatch.setattr(kit.band, "authorize", fail_later)
        path = f"/poppy/conversations/{created['conversation_id']}/events"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=kit.gateway.app, raise_app_exceptions=True
            ),
            base_url=ORIGIN,
        ) as client:
            result = await client.get(
                path,
                headers=kit.headers(access, "GET", path)
                | {"Accept": "text/event-stream"},
            )
        assert result.status_code == 200 and reads == 2
        assert '"id":"msg_one"' in result.text
        assert "PRIVATE_SECRET" not in result.text
        await kit.close()
