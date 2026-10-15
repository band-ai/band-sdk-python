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


_HEADER_SECRET = "req-secret"


def _post_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record for record in caplog.records if record.name == "band.platform.posting"
    ]


def _post_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in _post_records(caplog)]


def _parsing_error(
    payload: Any, *, status_code: int = 201, cause: BaseException | None = None
) -> ParsingError:
    if cause is None:
        with pytest.raises(ValidationError) as caught:
            CreateAgentChatMessageResponse.model_validate(payload)
        cause = caught.value
    return ParsingError(
        status_code=status_code,
        headers={"x-request-id": _HEADER_SECRET},
        body=payload,
        cause=cause,
    )


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
        assert _HEADER_SECRET not in error.detail
        posted = _post_records(caplog)
        assert [record.levelno for record in posted] == [logging.ERROR]
        assert _HEADER_SECRET not in posted[0].getMessage()
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
        posted = _post_records(caplog)
        assert [record.levelno for record in posted] == [logging.ERROR]
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

    async def test_a_present_non_string_handle_stays_a_parse_error(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = {
            "data": {
                "id": "msg-1",
                "success": True,
                "recipients": [{"id": "recip-1", "name": "Unknown", "handle": 1}],
            }
        }
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(payload)
        )

        with caplog.at_level(logging.ERROR), pytest.raises(ParsingError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert _post_logs(caplog) == []

    async def test_mixed_recipients_name_only_the_handle_less_one(
        self, mock_rest_client
    ) -> None:
        payload = {
            "data": {
                "id": "msg-1",
                "success": True,
                "recipients": [
                    {"id": "recip-1", "name": "Unknown"},
                    {"id": "recip-2", "name": "Peer", "handle": "peer-a"},
                    "not-a-recipient",
                ],
            }
        }
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(payload)
        )

        with pytest.raises(MessagePostError) as caught:
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert caught.value.detail == (
            "message msg-1 was created; id=recip-1 name=Unknown"
        )

    @pytest.mark.parametrize("present_handle", ["", None, 0])
    async def test_a_present_falsy_handle_is_not_named(
        self, mock_rest_client, present_handle: str | None | int
    ) -> None:
        payload = {
            "data": {
                "id": "msg-1",
                "success": True,
                "recipients": [
                    {"id": "blank", "name": "Blank", "handle": present_handle},
                    {"id": "gone", "name": "Gone"},
                ],
            }
        }
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(payload)
        )

        with pytest.raises(MessagePostError) as caught:
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert caught.value.detail == "message msg-1 was created; id=gone name=Gone"

    async def test_a_non_2xx_parse_error_stays_unclassified(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(_HANDLE_LESS, status_code=422)
        )

        with caplog.at_level(logging.ERROR), pytest.raises(ParsingError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert _post_logs(caplog) == []

    async def test_a_non_validation_cause_stays_unclassified(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            _parsing_error(_HANDLE_LESS, cause=ValueError("unparsed"))
        )

        with caplog.at_level(logging.ERROR), pytest.raises(ParsingError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert _post_logs(caplog) == []

    async def test_a_longer_422_message_stays_unprocessable(
        self, mock_rest_client, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            UnprocessableEntityError(
                body={
                    "error": {
                        "code": "validation_error",
                        "message": "mentioned_participant_not_in_room: extra",
                    }
                }
            )
        )

        with caplog.at_level(logging.ERROR), pytest.raises(UnprocessableEntityError):
            await post_message(
                rest=mock_rest_client,
                room_id="room-1",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )

        assert _post_logs(caplog) == []

    def test_rebuilt_from_its_message_keeps_the_classified_text(self) -> None:
        error = MessagePostError.classified(
            MessagePostKind.UNCONFIRMED_RECIPIENT,
            "message msg-1 was created; id=recip-1 name=Unknown",
        )

        rebuilt = type(error)(f"{error}; mentions=@peer")

        assert isinstance(rebuilt, MessagePostError)
        assert str(rebuilt) == (
            "unconfirmed_recipient: message msg-1 was created; "
            "id=recip-1 name=Unknown; mentions=@peer"
        )
