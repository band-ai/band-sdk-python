from __future__ import annotations

from band_rest.types.chat_message_metadata import ChatMessageMetadata

from band.core.types import USAGE_METADATA_KEY, is_usage_event


class TestIsUsageEvent:
    """The shared discriminator task-event consumers use to skip usage records."""

    def test_true_when_band_usage_present(self):
        assert is_usage_event({USAGE_METADATA_KEY: {"input_tokens": 1}}) is True

    def test_false_for_lifecycle_task_or_non_mapping(self):
        assert is_usage_event({"codex_thread_id": "x"}) is False
        assert is_usage_event(None) is False
        assert is_usage_event("nope") is False

    def test_true_and_false_for_chat_message_metadata_model(self):
        """band-client-rest 0.0.38 types ``ChatMessage.metadata`` as a model,
        not a dict — the discriminator must dump it, not require ``Mapping``."""
        assert (
            is_usage_event(ChatMessageMetadata(band_usage={"input_tokens": 1})) is True
        )
        assert is_usage_event(ChatMessageMetadata(claude_sdk_session_id="s")) is False
