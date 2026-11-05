"""Bind authenticated Teams activities to immutable local confirmation policy."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from microsoft_teams.api import AdaptiveCardInvokeActivity, MessageActivity
from microsoft_teams.api.models.adaptive_card.adaptive_card_action_response import (
    AdaptiveCardActionMessageResponse,
)
from microsoft_teams.apps import ActivityContext, App
from microsoft_teams.cards import AdaptiveCard
from microsoft_teams.common.http import ClientOptions

from bandteams.proposals import Dispatcher, Origin, Proposals, Refused
from bandteams.settings import (
    REQUEST_SECONDS,
    REVIEW_BYTES,
    Binding,
    Manifest,
    TeamsSettings,
)


class Action(StrEnum):
    SEND = "send"
    CANCEL = "cancel"


def envelope(activity: dict[str, Any], *, action: bool = False) -> Origin:
    data = activity.get("channelData") or {}
    return Origin(
        tenant=(data.get("tenant") or {}).get("id", ""),
        actor=(activity.get("from") or {}).get("aadObjectId", ""),
        team=(data.get("team") or {}).get("id", ""),
        channel=(data.get("channel") or {}).get("id", ""),
        conversation=(activity.get("conversation") or {}).get("id", ""),
        activity=activity.get("replyToId" if action else "id") or "",
    )


def plain_reply(activity: dict[str, Any]) -> str:
    body = activity.get("text") or ""
    if (
        activity.get("attachments")
        or activity.get("textFormat") not in (None, "plain")
        or "<" in body
        or ">" in body
    ):
        raise Refused(
            "Use plain text without attachments; your reply has not been sent"
        )
    if not body.strip():
        raise Refused("Reply is empty")
    return body


def review_card(
    proposal: str, version: int, room: str, recipient: str, body: str
) -> AdaptiveCard:
    card = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": f"Review — not sent to Band\nProposal: {proposal}",
                "weight": "Bolder",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"Room: {room}\nRecipient: {recipient}",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", body),
                "wrap": True,
            },
        ],
        "actions": [
            {
                "type": "Action.Execute",
                "title": title,
                "verb": verb,
                "data": {"proposal": proposal, "version": version},
            }
            for title, verb in (
                ("Send to Band", Action.SEND),
                ("Cancel", Action.CANCEL),
            )
        ],
    }
    if len(json.dumps(card).encode("utf-16-le")) > REVIEW_BYTES:
        raise Refused(
            "Complete review exceeds the content envelope; shorten and resubmit"
        )
    return AdaptiveCard.model_validate(card)


class Teams:
    """Keep action acknowledgement independent of remote dispatch."""

    def __init__(
        self,
        app: App,
        manifest: Manifest,
        proposals: Proposals,
        dispatcher: Dispatcher,
        *,
        authorize: Callable[[str, str], Binding] | None = None,
    ) -> None:
        self.person = authorize or manifest.person
        self.app = app
        self.manifest = manifest
        self.proposals = proposals
        self.dispatcher = dispatcher

    async def action(
        self, ctx: ActivityContext[AdaptiveCardInvokeActivity]
    ) -> AdaptiveCardActionMessageResponse:
        activity = ctx.activity.model_dump(by_alias=True, exclude_none=True)
        try:
            origin = envelope(activity, action=True)
            self.person(origin.tenant, origin.actor)
            action = ctx.activity.value.action
            proposal = action.data["proposal"]
            version = action.data["version"]
            if not isinstance(proposal, str) or type(version) is not int:
                raise Refused("Invalid action")
            match action.verb:
                case Action.SEND:
                    claim = await self.dispatcher.confirm(proposal, version, origin)
                case Action.CANCEL:
                    claim = self.proposals.cancel(proposal, version, origin)
                case _:
                    raise Refused("Unsupported action")
            result = claim.delivery.value
        except (ValueError, KeyError):
            result = "Action refused; use your current review in its original thread"
        return AdaptiveCardActionMessageResponse(value=result)

    async def stage(
        self, ctx: ActivityContext[MessageActivity], room: str, recipient: str
    ) -> None:
        activity = ctx.activity.model_dump(by_alias=True, exclude_none=True)
        origin = envelope(activity)
        person = self.person(origin.tenant, origin.actor)
        body = plain_reply(activity)
        claim = self.proposals.stage(person, room, recipient, origin, body)
        card = review_card(claim.proposal_id, claim.version, room, recipient, body)
        sent = await ctx.send(card)
        self.proposals.journal.resource(person.alias, origin.conversation, sent.id)
        self.proposals.arm(claim.proposal_id, sent.id)


def application(settings: TeamsSettings) -> App:
    return App(
        client_id=settings.CLIENT_ID,
        client_secret=settings.CLIENT_SECRET.get_secret_value(),
        tenant_id=settings.TENANT_ID,
        dangerously_allow_unauthenticated_requests=False,
        fetch_user_token=False,
        client=ClientOptions(timeout=REQUEST_SECONDS),
    )
