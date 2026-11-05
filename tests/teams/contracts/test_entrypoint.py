"""Exercise native reply staging and action acknowledgement through real Teams contexts."""

from __future__ import annotations

import json
from typing import Any

from bandteams.evidence import Delivery
from bandteams.teams import Action
from microsoft_teams.api import AdaptiveCardInvokeActivity, MessageActivity


class TestEntrypoint:
    async def test_native_reply_requires_confirmation_and_duplicate_invoke_cannot_enqueue(
        self, harness: Any
    ) -> None:
        ctx = harness.context(harness.activity, MessageActivity)
        await harness.teams.stage(ctx, "one", "target")
        assert harness.sends == []
        request = json.loads(harness.requests[0].content)
        card = request["attachments"][0]["content"]
        action = card["actions"][0]
        assert (card["body"][-1]["text"], action["verb"]) == ("Exact body", Action.SEND)
        data = {
            **harness.activity,
            "type": "invoke",
            "name": "adaptiveCard/action",
            "id": "invoke",
            "replyToId": "review-id",
            "value": {"action": action},
        }
        invoke = harness.context(data, AdaptiveCardInvokeActivity)
        first = await harness.teams.action(invoke)
        await harness.checking.wait()
        duplicate = await harness.teams.action(invoke)
        assert (first.value, duplicate.value, harness.sends) == (
            Delivery.PENDING,
            Delivery.PENDING,
            [],
        )
        worker = next(iter(harness.dispatcher.tasks.values()))
        harness.release.set()
        await worker
        assert harness.sends == [("target", "Exact body")]

    async def test_unsigned_ingress_is_rejected_by_sdk(self, harness: Any) -> None:
        harness.app.server.initialize(credentials=harness.app.credentials)
        response = await harness.app.server.handle_request(
            {"body": harness.activity, "headers": {}}
        )
        assert response == {"status": 401, "body": {"error": "Unauthorized"}}
