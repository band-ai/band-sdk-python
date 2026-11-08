"""Authenticated PAP conversation creation, retry and pagination."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pap.gatewaykit import GatewayHarness, message, reply


class TestGatewayConversations:
    async def test_create_send_read_and_user_wide_first_message_retry(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        access = kit.access()
        first = await kit.request(
            access,
            "POST",
            "/poppy/conversations",
            value=message(data={"rows": [[1, False]]}, context={"locale": "en-US"}),
        )
        assert first.status_code == 201
        conversation = first.json()["conversation_id"]
        retry = await kit.request(
            access,
            "POST",
            "/poppy/conversations",
            value=message(data={"rows": [[1, False]]}, context={"locale": "en-US"}),
        )
        assert retry.json() == first.json() and retry.status_code == 201
        conflict = await kit.request(
            access, "POST", "/poppy/conversations", value=message(text="different")
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["error"] == "message_id_conflict"
        )
        sent = await kit.request(
            access,
            "POST",
            f"/poppy/conversations/{conversation}/messages",
            value=message("msg_two", context={"user_available": False}),
        )
        assert sent.status_code == 202
        await kit.gateway.publish(
            access,
            conversation,
            reply(),
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        result = await kit.request(
            access, "GET", f"/poppy/conversations/{conversation}/events"
        )
        assert result.status_code == 200 and result.json()["has_more"]
        assert result.json()["events"][0]["message"]["data"] == {"rows": [[1, False]]}
        cursor = result.json()["cursor"]
        remainder = await kit.request(
            access, "GET", f"/poppy/conversations/{conversation}/events?cursor={cursor}"
        )
        assert [event["type"] for event in remainder.json()["events"]] == [
            "message",
            "state",
        ]
        cursor = remainder.json()["cursor"]
        empty = await kit.request(
            access,
            "GET",
            f"/poppy/conversations/{conversation}/events?cursor={cursor}&wait=0",
        )
        assert empty.json()["events"] == [] and empty.json()["cursor"] == cursor
        assert await kit.gateway.flush() == 2
        assert len(kit.band.accepted) == 2
        latest = list(kit.band.accepted.values())[-1]
        assert latest.context == {"locale": "en-US", "user_available": False}
        await kit.close()

    @pytest.mark.parametrize(
        "change",
        [
            {"user": "bob"},
            {"client": "https://other.example/client"},
            {"tenant": "other"},
        ],
    )
    async def test_guessed_conversation_and_cursor_never_grant_access(
        self, tmp_path: Path, change: dict
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        owner = kit.access()
        created = await kit.request(
            owner, "POST", "/poppy/conversations", value=message()
        )
        id_ = created.json()["conversation_id"]
        outsider = kit.access(session_id="outsider", **change)
        for method, suffix, value in [
            ("GET", "/events", None),
            ("POST", "/messages", message("msg_other")),
            ("POST", "/close", {}),
        ]:
            response = await kit.request(
                outsider, method, f"/poppy/conversations/{id_}{suffix}", value=value
            )
            assert response.status_code == 404 and response.json() == {
                "error": "conversation_not_found"
            }
        other = await kit.request(
            outsider, "POST", "/poppy/conversations", value=message("msg_other")
        )
        page = await kit.request(owner, "GET", f"/poppy/conversations/{id_}/events")
        wrong = await kit.request(
            outsider,
            "GET",
            f"/poppy/conversations/{other.json()['conversation_id']}/events?cursor={page.json()['cursor']}",
        )
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_cursor"
        await kit.close()

    async def test_direct_conversation_suspends_parent_and_close_restores_it(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=100)
        access = kit.access()
        parent = (
            await kit.request(access, "POST", "/poppy/conversations", value=message())
        ).json()["conversation_id"]
        direct = await kit.request(
            access,
            "POST",
            "/poppy/conversations",
            value=message("msg_human", sender="human", text="My own words")
            | {"parent_conversation_id": parent},
        )
        child = direct.json()["conversation_id"]
        assert direct.status_code == 201
        for suffix, value in [("messages", message("msg_blocked")), ("handoff", {})]:
            blocked = await kit.request(
                access, "POST", f"/poppy/conversations/{parent}/{suffix}", value=value
            )
            assert (
                blocked.status_code == 409
                and blocked.json()["conversation_id"] == child
            )
        nested = await kit.request(
            access,
            "POST",
            "/poppy/conversations",
            value=message("msg_nested", sender="human")
            | {"parent_conversation_id": child},
        )
        assert nested.status_code == 404
        closed = await kit.request(
            access, "POST", f"/poppy/conversations/{child}/close", value={}
        )
        assert closed.status_code == 200
        sent = await kit.request(
            access,
            "POST",
            f"/poppy/conversations/{parent}/messages",
            value=message("msg_restored"),
        )
        assert sent.status_code == 202
        events = (
            await kit.request(access, "GET", f"/poppy/conversations/{parent}/events")
        ).json()["events"]
        assert [event["type"] for event in events] == [
            "message",
            "direct_opened",
            "direct_closed",
            "message",
        ]
        await kit.close()

    @pytest.mark.parametrize("wait", ["-1", "1.5", "true", "garbage"])
    async def test_malformed_wait_rejects_before_acceptance(
        self, tmp_path: Path, wait: str
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db")
        response = await kit.request(
            kit.access(), "POST", f"/poppy/conversations?wait={wait}", value=message()
        )
        assert response.status_code == 400
        assert (
            kit.store.db.execute("SELECT count(*) FROM gateway_messages").fetchone()[0]
            == 0
        )
        await kit.close()

    async def test_post_wait_response_is_durable_and_stable_on_retry(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "gateway.db", page_size=100)
        access = kit.access()
        first = await kit.request(
            access, "POST", "/poppy/conversations?wait=0", value=message()
        )
        assert first.status_code == 201 and len(first.json()["events"]) == 1
        id_ = first.json()["conversation_id"]
        await kit.gateway.publish(
            access,
            id_,
            reply(),
            source_id="company",
            source_type="Agent",
            recipients=("proxy-alice",),
        )
        retried = await kit.request(
            access, "POST", "/poppy/conversations?wait=0", value=message()
        )
        assert retried.json() == first.json()
        assert "participants" not in first.json()
        await kit.close()
