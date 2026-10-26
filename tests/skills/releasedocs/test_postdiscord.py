"""The Discord poster posts with the right mentions, returns the message id, and deletes by id."""

from __future__ import annotations

import json
from types import ModuleType

import pytest

from tests.skills.releasedocs.webhookpeer import Received, WebhookPeer

MESSAGE = "@everyone\n\n**Band Python SDK v6.1.0** is live!"
WEBHOOK_PATH = "/api/webhooks/1/token"


def posted(text: str, parse: list[str]) -> bytes:
    payload = {"content": text, "allowed_mentions": {"parse": parse}}
    return json.dumps(payload, ensure_ascii=False).encode()


class TestPostDiscord:
    @pytest.mark.parametrize(("mentions", "parse"), [(True, ["everyone"]), (False, [])])
    def test_post_waits_for_the_message_id(
        self,
        postdiscord: ModuleType,
        peer: WebhookPeer,
        mentions: bool,
        parse: list[str],
    ) -> None:
        peer.reply(200, b'{"id": "1234567890"}')
        assert (
            postdiscord.post_message(peer.url, MESSAGE, mentions=mentions)
            == "1234567890"
        )
        assert peer.received == [
            Received("POST", f"{WEBHOOK_PATH}?wait=true", posted(MESSAGE, parse))
        ]

    def test_delete_targets_the_message(
        self, postdiscord: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(204)
        postdiscord.delete_message(peer.url, "1234567890")
        assert peer.received == [
            Received("DELETE", f"{WEBHOOK_PATH}/messages/1234567890", b"")
        ]

    def test_non_2xx_answer_fails(
        self, postdiscord: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(404, b'{"message": "Unknown Webhook"}')
        with pytest.raises(RuntimeError, match="HTTP 404"):
            postdiscord.post_message(peer.url, MESSAGE)

    def test_non_discord_webhook_url_is_rejected(
        self,
        postdiscord: ModuleType,
        peer: WebhookPeer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", peer.url)
        assert postdiscord.main(["--delete", "1234567890"]) == 1
        assert peer.received == []
