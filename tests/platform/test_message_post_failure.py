"""Classified chat-message post failures.

A 2xx whose recipient has no handle, and a 422 ``mentioned_participant_not_in_room``,
become :class:`MessagePostError` with a stable kind. The log line and the
exception text name that kind and the recipient, not response headers.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from band_rest.agent_api_messages.types.create_agent_chat_message_response import (
    CreateAgentChatMessageResponse,
)
from band_rest.core.parse_error import ParsingError
from band_rest.errors import UnprocessableEntityError
from pydantic import ValidationError

from band.client.rest import ChatMessageRequest
from band.core.exceptions import MessagePostError, MessagePostKind
from band.platform.posting import post_message

_HANDLE_LESS = {
    "data": {
        "id": "msg-1",
        "success": True,
        "recipients": [{"id": "recip-1", "name": "Unknown"}],
    }
}


def _post_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "band.platform.posting"
    ]


def _parsing_error(payload: Any) -> ParsingError:
    with pytest.raises(ValidationError) as caught:
        CreateAgentChatMessageResponse.model_validate(payload)
    return ParsingError(status_code=201, body=payload, cause=caught.value)


class TestMessagePostFailure:
    async def test_unconfirmed_recipient_names_the_created_message(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(_HANDLE_LESS)
        )

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(MessagePostError) as caught,
        ):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        error = caught.value
        assert error.kind is MessagePostKind.UNCONFIRMED_RECIPIENT
        assert error.detail == ("message msg-1 was created; id=recip-1 name=Unknown")
        assert "headers" not in str(error)
        assert _post_logs(caplog) == [
            (
                "message post in room room-1 failed: "
                "unconfirmed_recipient: message msg-1 was created; "
                "id=recip-1 name=Unknown"
            )
        ]

    async def test_other_parse_errors_stay_unclassified(self, mock_rest_client) -> None:
        payload = {
            "data": {
                "success": True,
                "recipients": [{"id": "recip-1", "handle": "peer-a"}],
            }
        }
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(payload)
        )

        with pytest.raises(ParsingError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

    async def test_mention_not_in_room_uses_the_platform_token(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            UnprocessableEntityError(
                body={
                    "error": {
                        "code": "validation_error",
                        "message": "mentioned_participant_not_in_room",
                    }
                }
            )
        )

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(MessagePostError) as caught,
        ):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert caught.value.kind is MessagePostKind.MENTION_NOT_IN_ROOM
        assert _post_logs(caplog) == [
            (
                "message post in room room-1 failed: "
                "mentioned_participant_not_in_room: "
                "mentioned participant is not in the room"
            )
        ]

    async def test_other_rejections_stay_unprocessable(self, mock_rest_client) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            UnprocessableEntityError(
                body={
                    "error": {
                        "code": "validation_error",
                        "message": "unknown_attachment",
                    }
                }
            )
        )

        with pytest.raises(UnprocessableEntityError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )
