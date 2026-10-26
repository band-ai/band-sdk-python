"""Read the peer's recorded native request frames."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpCommandName,
)
from tests.integrations.ompscenarios import PeerCommand


async def checkpoint_frames(client: OmpRpcClient) -> list[dict[str, JsonValue]]:
    checkpoint = await client.request(PeerCommand.CHECKPOINT)
    return cast(list[dict[str, JsonValue]], checkpoint["frames"])


async def sent_prompt_messages(client: OmpRpcClient) -> list[str]:
    return [
        cast(str, frame["message"])
        for frame in await checkpoint_frames(client)
        if frame["type"] == OmpCommandName.PROMPT
    ]


async def sent_command_parameters(
    client: OmpRpcClient, command: OmpCommandName
) -> list[dict[str, JsonValue]]:
    return [
        {key: value for key, value in frame.items() if key not in {"type", "id"}}
        for frame in await checkpoint_frames(client)
        if frame["type"] == command
    ]
