"""The Slack poster sends the message to the incoming webhook and fails on any error."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.webhookpeer import WEBHOOK_PATH, Received, WebhookPeer

# Nothing listens on port 1 (tcpmux), so the connection is refused.
UNREACHABLE_URL = f"http://127.0.0.1:1{WEBHOOK_PATH}"
MESSAGE = "*Band Python SDK v6.1.0* is live! — ünïcode"


class TestPostSlack:
    def test_message_posts_as_slack_text(
        self, postslack: ModuleType, webhooks: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(200, b"ok")
        postslack.post_message(peer.url, MESSAGE)
        assert peer.received == [
            Received(
                "POST",
                WEBHOOK_PATH,
                json.dumps({"text": MESSAGE}, ensure_ascii=False).encode(),
                webhooks.USER_AGENT,
            )
        ]

    def test_non_2xx_answer_fails(
        self, postslack: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(403, b"invalid_token")
        with pytest.raises(RuntimeError, match="HTTP 403: invalid_token"):
            postslack.post_message(peer.url, MESSAGE)

    def test_unreachable_webhook_fails(self, postslack: ModuleType) -> None:
        with pytest.raises(RuntimeError, match="Slack webhook request failed"):
            postslack.post_message(UNREACHABLE_URL, MESSAGE)

    def test_non_slack_webhook_url_is_rejected(
        self,
        postslack: ModuleType,
        peer: WebhookPeer,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", peer.url)
        message = tmp_path / "message.txt"
        message.write_text(MESSAGE, encoding="utf-8")
        assert postslack.main(["--message-file", str(message)]) == 1
        assert peer.received == []
