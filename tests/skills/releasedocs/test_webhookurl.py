"""A poster only sends to a webhook URL on its service's host."""

from __future__ import annotations

from types import ModuleType

import pytest
from pydantic import SecretStr

SLACK = "slack"
DISCORD = "discord"
SLACK_URL = "https://hooks.slack.com/services/T/B/x"
DISCORD_URL = "https://discord.com/api/webhooks/1/token"
LEGACY_DISCORD_URL = "https://discordapp.com/api/webhooks/1/token"


@pytest.fixture
def prefixes(postslack: ModuleType, postdiscord: ModuleType) -> dict[str, object]:
    return {
        SLACK: postslack.SLACK_WEBHOOK_PREFIXES,
        DISCORD: postdiscord.DISCORD_WEBHOOK_PREFIXES,
    }


class TestWebhookUrl:
    @pytest.mark.parametrize(
        ("service", "url"),
        [(SLACK, SLACK_URL), (DISCORD, DISCORD_URL), (DISCORD, LEGACY_DISCORD_URL)],
    )
    def test_service_url_is_accepted(
        self,
        webhooks: ModuleType,
        prefixes: dict[str, object],
        service: str,
        url: str,
    ) -> None:
        assert webhooks.webhook_url("URL", SecretStr(url), prefixes[service]) == url

    @pytest.mark.parametrize(
        ("service", "url"),
        [
            (SLACK, DISCORD_URL),
            (DISCORD, SLACK_URL),
            (DISCORD, "http://discord.com/api/webhooks/1/token"),
        ],
    )
    def test_foreign_url_is_refused(
        self,
        webhooks: ModuleType,
        prefixes: dict[str, object],
        service: str,
        url: str,
    ) -> None:
        with pytest.raises(ValueError, match="URL must start with"):
            webhooks.webhook_url("URL", SecretStr(url), prefixes[service])

    def test_missing_url_is_refused(
        self, webhooks: ModuleType, prefixes: dict[str, object]
    ) -> None:
        with pytest.raises(ValueError, match="URL is required"):
            webhooks.webhook_url("URL", None, prefixes[SLACK])
