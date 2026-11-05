"""Drive real Teams contexts with an external HTTP transport peer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from bandteams.evidence import Journal
from bandteams.proposals import Dispatcher, Proposals
from bandteams.settings import Binding, Manifest, Room, TeamsSettings
from bandteams.teams import Teams, application
from microsoft_teams.api import ApiClient, ConversationReference
from microsoft_teams.apps import ActivityContext
from microsoft_teams.common import Client, LocalStorage
from microsoft_teams.common.http import ClientOptions


@pytest.fixture
async def harness(tmp_path: Any) -> Any:
    person = Binding(
        alias="alice",
        tenant_id="tenant",
        actor_id="entra",
        band_user_id="band-user",
        team_id="team",
        channel_id="channel",
        group_id="group",
        rooms=(
            Room(id="one", recipients=("target",)),
            Room(id="two", recipients=("target",)),
        ),
        private_owner_verified=True,
        rsc_consent_verified=True,
    )
    bob = person.model_copy(
        update={
            "alias": "bob",
            "actor_id": "bob",
            "band_user_id": "bob",
            "team_id": "bob",
            "channel_id": "bob",
            "group_id": "bob",
        }
    )
    manifest = Manifest(
        people=(person, bob),
        ingress_url="https://operator.test/api/messages",
        deployment="stable-server",
        installed_manifest=Path("manifest.json"),
        entitlement_evidence=Path("admission.json"),
        teams_client_version="test",
        posts_layout_verified=True,
    )
    journal = Journal(tmp_path / "state")
    proposals = Proposals(journal)
    proposals.activate(person)
    requests = []

    def peer(request: Any) -> Any:
        if request.method != "POST" or not request.url.path.endswith("/activities"):
            raise AssertionError(
                f"Unexpected Teams request {request.method} {request.url.path}"
            )
        requests.append(request)
        return httpx.Response(200, json={"id": "review-id"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(peer))
    client = Client(ClientOptions(token="synthetic-bot-token"), _http=http)
    api = ApiClient("https://teams.test", client)
    checking, release = (asyncio.Event(), asyncio.Event())
    sends = []

    async def check(claim: Any) -> Any:
        checking.set()
        await release.wait()

    async def send(claim: Any, body: Any) -> Any:
        sends.append((claim.recipient, body))
        return "00000000-0000-0000-0000-000000000001"

    async def receipt(claim: Any) -> Any:
        return None

    dispatcher = Dispatcher(proposals, check, send, receipt)
    app = application(
        TeamsSettings(CLIENT_ID="app", CLIENT_SECRET="secret", TENANT_ID="tenant")
    )
    teams = Teams(app, manifest, proposals, dispatcher)
    activity = {
        "type": "message",
        "id": "native-reply",
        "channelId": "msteams",
        "serviceUrl": "https://teams.test",
        "from": {"id": "teams-user", "aadObjectId": "entra"},
        "recipient": {"id": "bot"},
        "conversation": {"id": "thread", "conversationType": "channel"},
        "text": "Exact body",
        "textFormat": "plain",
        "channelData": {
            "tenant": {"id": "tenant"},
            "team": {"id": "team"},
            "channel": {"id": "channel"},
        },
    }

    def context(data: Any, model: Any) -> Any:
        parsed = model.model_validate(data)
        ref = ConversationReference(
            service_url="https://teams.test",
            channel_id="msteams",
            bot=parsed.recipient,
            conversation=parsed.conversation,
            user=parsed.from_,
        )
        return ActivityContext(
            parsed, "app", LocalStorage(), api, None, ref, False, "", SimpleNamespace()
        )

    yield SimpleNamespace(
        manifest=manifest,
        person=person,
        journal=journal,
        api=api,
        teams=teams,
        app=app,
        proposals=proposals,
        dispatcher=dispatcher,
        activity=activity,
        context=context,
        checking=checking,
        release=release,
        sends=sends,
        requests=requests,
    )
    await dispatcher.stop(person.alias)
    journal.close()
    await http.aclose()
    await app.http_client.http.aclose()
