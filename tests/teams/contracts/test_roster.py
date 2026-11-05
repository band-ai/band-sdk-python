"""Require the complete Teams roster before admitting a private destination."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import httpx
import pytest
from bandteams.proposals import Refused
from bandteams.service import Service
from bandteams.settings import BandSettings
from microsoft_teams.common import Client
from microsoft_teams.common.http import ClientOptions


class TestRoster:
    @pytest.mark.parametrize(
        "second_member,expectation",
        [
            (None, nullcontext()),
            ("another-employee", pytest.raises(Refused, match="exactly")),
        ],
    )
    async def test_all_roster_pages_must_contain_only_the_linked_employee(
        self, harness: Any, tmp_path: Any, second_member: Any, expectation: Any
    ) -> None:
        manifest_file = tmp_path / "run.json"
        manifest_file.write_text(harness.manifest.model_dump_json())
        service = Service(
            harness.app,
            harness.manifest,
            BandSettings(BAND_OAUTH_CLIENT_ID="client"),
            harness.journal,
            manifest_file,
        )
        paths = []

        def peer(request: Any) -> Any:
            paths.append(str(request.url))
            assert request.url.path == "/teams/v3/conversations/team/pagedMembers"
            match request.url.params.get("continuationToken"):
                case None:
                    return httpx.Response(
                        200,
                        json={
                            "members": [{"id": "teams-user", "aadObjectId": "entra"}],
                            "continuationToken": "next",
                        },
                    )
                case "next":
                    members = (
                        []
                        if second_member is None
                        else [{"id": second_member, "aadObjectId": second_member}]
                    )
                    return httpx.Response(200, json={"members": members})
                case _:
                    raise AssertionError("Unexpected roster cursor")

        async with httpx.AsyncClient(transport=httpx.MockTransport(peer)) as http:
            harness.app.api.conversations.http = Client(
                ClientOptions(token="synthetic"), _http=http
            )
            try:
                with expectation:
                    await service.roster(harness.person)
                assert len(paths) == 2
            finally:
                await service.close()
