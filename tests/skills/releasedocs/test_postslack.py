"""The Slack poster sends the message to the incoming webhook and fails on any error."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.webhookpeer import Received, WebhookPeer

MESSAGE = "*Band Python SDK v6.1.0* is live! — ünïcode"


class TestPostSlack:
    def test_message_posts_as_slack_text(
        self, postslack: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(200, b"ok")
        postslack.post_message(peer.url, MESSAGE)
        assert peer.received == [
            Received(
                "POST",
                "/api/webhooks/1/token",
                json.dumps({"text": MESSAGE}, ensure_ascii=False).encode(),
            )
        ]

    def test_non_2xx_answer_fails(
        self, postslack: ModuleType, peer: WebhookPeer
    ) -> None:
        peer.reply(403, b"invalid_token")
        with pytest.raises(RuntimeError, match="HTTP 403: invalid_token"):
            postslack.post_message(peer.url, MESSAGE)

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

    def test_stdin_is_read_as_utf8(
        self, webhooks: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdin = io.TextIOWrapper(io.BytesIO(MESSAGE.encode("utf-8")), encoding="cp1252")
        monkeypatch.setattr(sys, "stdin", stdin)
        assert webhooks.read_message(None) == MESSAGE
