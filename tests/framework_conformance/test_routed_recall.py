"""The routed-recall failure names which miss it was.

An empty mention set is ``no_mention_of_peer``, not a report that the exact
value was absent from a blank haystack. The failure lists the mention ids on
the messages the sender did post.
"""

from __future__ import annotations

import pytest

from band.client.streaming import Mention, MessageCreatedPayload, MessageMetadata
from tests.e2e.baseline.toolkit.observations.replies import Replies, RoutedRecallFailure


def _reply(
    content: str, *mention_ids: str, omit_metadata: bool = False
) -> MessageCreatedPayload:
    now = "2026-01-01T00:00:00Z"
    metadata = None
    if not omit_metadata:
        metadata = MessageMetadata(
            mentions=[Mention(id=mention_id) for mention_id in mention_ids]
        )
    return MessageCreatedPayload(
        id="m",
        content=content,
        message_type="text",
        metadata=metadata,
        sender_id="sender",
        sender_type="Agent",
        inserted_at=now,
        updated_at=now,
    )


class TestRoutedRecallFailure:
    def test_returns_only_the_message_that_mentions_the_peer_with_the_value(
        self,
    ) -> None:
        routed = Replies(
            [
                _reply("status only", "peer-a"),
                _reply("VALUE1", "peer-a"),
                _reply("VALUE1", "user-1"),
            ]
        ).assert_routed_value("peer-a", "VALUE1")

        assert [message.content for message in routed] == ["VALUE1"]
        assert routed[0].metadata is not None
        assert [mention.id for mention in routed[0].metadata.mentions] == ["peer-a"]

    @pytest.mark.parametrize(
        ("replies", "kind", "shown"),
        [
            (Replies(), RoutedRecallFailure.NO_SENDER_MESSAGE, "(no messages)"),
            (
                Replies([_reply("VALUE1", "user-1")]),
                RoutedRecallFailure.NO_MENTION_OF_PEER,
                "mentions=user-1 | VALUE1",
            ),
            (
                Replies([_reply("hello", "user-1", "user-2")]),
                RoutedRecallFailure.NO_MENTION_OF_PEER,
                "mentions=user-1,user-2 | hello",
            ),
            (
                Replies([_reply("VALUE1")]),
                RoutedRecallFailure.NO_MENTION_OF_PEER,
                "mentions=none | VALUE1",
            ),
            (
                Replies([_reply("VALUE1", omit_metadata=True)]),
                RoutedRecallFailure.NO_MENTION_OF_PEER,
                "mentions=none | VALUE1",
            ),
            (
                Replies([_reply("hello", "peer-a")]),
                RoutedRecallFailure.MENTION_OMITTED_VALUE,
                "mentions=peer-a | hello",
            ),
            (
                Replies([_reply("VALUE1", "user-1"), _reply("hello", "peer-a")]),
                RoutedRecallFailure.MENTION_OMITTED_VALUE,
                "mentions=user-1 | VALUE1\nmentions=peer-a | hello",
            ),
        ],
    )
    def test_names_the_miss_and_the_messages_that_were_posted(
        self,
        replies: Replies,
        kind: RoutedRecallFailure,
        shown: str,
    ) -> None:
        with pytest.raises(AssertionError, match=kind) as caught:
            replies.assert_routed_value("peer-a", "VALUE1")

        assert shown in str(caught.value)

    def test_rejects_an_empty_expected_value(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            Replies([_reply("VALUE1", "peer-a")]).assert_routed_value("peer-a", "")
